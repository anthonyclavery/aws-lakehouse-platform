import hashlib
import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from threading import Barrier, Lock
from unittest.mock import Mock

from lakehouse_platform.landing import ImmutableObjectConflict, LandingBatchRejected, plan_landing_upload, upload_landing_batch


class S3Error(Exception):
    def __init__(self, code, status):
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.lock = Lock()
        self.puts = []

    def get_object(self, *, Bucket, Key):
        with self.lock:
            if (Bucket, Key) not in self.objects:
                raise S3Error("NoSuchKey", 404)
            return {"Body": io.BytesIO(self.objects[(Bucket, Key)]["body"])}

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch, ChecksumSHA256, Metadata):
        self.assert_condition(IfNoneMatch)
        body = Body.read()
        with self.lock:
            self.puts.append(Key)
            if (Bucket, Key) in self.objects:
                raise S3Error("PreconditionFailed", 412)
            if base64.b64encode(hashlib.sha256(body).digest()).decode() != ChecksumSHA256:
                raise S3Error("BadDigest", 400)
            self.objects[(Bucket, Key)] = {"body": body, "metadata": Metadata}
        return {}

    @staticmethod
    def assert_condition(value):
        if value != "*":
            raise AssertionError("An upload attempted an unconditional write")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_batch(root: Path) -> Path:
    batch = root / "batch"
    source = batch / "reference" / "site_meter.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text('{"meter_id":"synthetic"}\n', encoding="utf-8")
    (batch / "manifest.json").write_text(json.dumps({"files": [{"path": "reference/site_meter.jsonl", "sha256": sha256(source)}]}), encoding="utf-8")
    return batch


class LandingTests(unittest.TestCase):
    def test_plan_keeps_manifest_last(self):
        with TemporaryDirectory() as directory:
            planned = plan_landing_upload(create_batch(Path(directory)), "initial-20261016")
        self.assertEqual("landing/batch_id=initial-20261016/reference/site_meter.jsonl", planned[0].key)
        self.assertEqual("landing/batch_id=initial-20261016/manifest.json", planned[-1].key)

    def test_rejects_tampered_file(self):
        with TemporaryDirectory() as directory:
            batch = create_batch(Path(directory))
            (batch / "reference" / "site_meter.jsonl").write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(LandingBatchRejected):
                plan_landing_upload(batch, "initial-20261016")

    def test_reuses_matching_immutable_objects_and_rejects_conflicts(self):
        with TemporaryDirectory() as directory:
            batch = create_batch(Path(directory))
            client = FakeS3()
            first = upload_landing_batch(client, "bucket", batch, "initial-20261016")
            second = upload_landing_batch(client, "bucket", batch, "initial-20261016")
            self.assertEqual(2, len(first.uploaded_keys))
            self.assertEqual(2, len(second.existing_keys))
            client.objects[("bucket", "landing/batch_id=initial-20261016/reference/site_meter.jsonl")]["body"] = b"different"
            with self.assertRaises(ImmutableObjectConflict):
                upload_landing_batch(client, "bucket", batch, "initial-20261016")

    def test_legacy_object_without_metadata_is_never_overwritten(self):
        with TemporaryDirectory() as directory:
            batch = create_batch(Path(directory))
            client = FakeS3()
            key = "landing/batch_id=atomic-test/reference/site_meter.jsonl"
            client.objects[("bucket", key)] = {"body": b"old data", "metadata": {}}
            with self.assertRaises(ImmutableObjectConflict):
                upload_landing_batch(client, "bucket", batch, "atomic-test")
            self.assertEqual(b"old data", client.objects[("bucket", key)]["body"])
            self.assertEqual([key], client.puts)
            self.assertNotIn(("bucket", "landing/batch_id=atomic-test/manifest.json"), client.objects)

    def test_matching_bytes_can_replay_even_with_missing_or_wrong_metadata(self):
        with TemporaryDirectory() as directory:
            batch = create_batch(Path(directory))
            client = FakeS3()
            upload_landing_batch(client, "bucket", batch, "atomic-test")
            for obj in client.objects.values():
                obj["metadata"] = {"sha256": "incorrect"}
            result = upload_landing_batch(client, "bucket", batch, "atomic-test")
            self.assertEqual(2, len(result.existing_keys))
            self.assertFalse(result.uploaded_keys)

    def test_two_concurrent_writers_publish_one_identical_batch(self):
        class RacingS3(FakeS3):
            barrier = Barrier(2)

            def put_object(self, **kwargs):
                if kwargs["Key"].endswith("site_meter.jsonl"):
                    self.barrier.wait(timeout=5)
                return super().put_object(**kwargs)
        with TemporaryDirectory() as directory:
            batch = create_batch(Path(directory))
            client = RacingS3()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: upload_landing_batch(client, "bucket", batch, "atomic-test"), range(2)))
            self.assertEqual(2, len(client.objects))
            self.assertEqual(2, sum(len(r.uploaded_keys) for r in results))
            self.assertEqual(2, sum(len(r.existing_keys) for r in results))

    def test_other_failures_do_not_read_existing_or_publish_manifest(self):
        with TemporaryDirectory() as directory:
            batch = create_batch(Path(directory))
            for code, status in (("AccessDenied", 403), ("ConditionalRequestConflict", 409),
                                 ("NoSuchKey", 404), ("PreconditionFailed", 403)):
                client = Mock()
                error = S3Error(code, status)
                client.put_object.side_effect = error
                with self.assertRaises(S3Error) as raised:
                    upload_landing_batch(client, "bucket", batch, "atomic-test")
                self.assertIs(error, raised.exception)
                client.get_object.assert_not_called()
                self.assertEqual(1, client.put_object.call_count)

    def test_conflicting_concurrent_writer_cannot_publish_its_manifest(self):
        class RacingS3(FakeS3):
            barrier = Barrier(2)

            def put_object(self, **kwargs):
                if kwargs["Key"].endswith("site_meter.jsonl"):
                    self.barrier.wait(timeout=5)
                return super().put_object(**kwargs)
        with TemporaryDirectory() as directory:
            batches = [create_batch(Path(directory) / name) for name in ("first", "second")]
            source = batches[1] / "reference/site_meter.jsonl"
            source.write_text('{"meter_id":"other-synthetic"}\n')
            manifest = batches[1] / "manifest.json"
            manifest.write_text(json.dumps({"files": [{"path": "reference/site_meter.jsonl", "sha256": sha256(source)}]}))
            client = RacingS3()
            def upload(batch):
                try:
                    upload_landing_batch(client, "bucket", batch, "atomic-test")
                    return batch
                except ImmutableObjectConflict:
                    return None
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(upload, batches))
            winners = [batch for batch in results if batch is not None]
            self.assertEqual(1, len(winners))
            for item in plan_landing_upload(winners[0], "atomic-test"):
                self.assertEqual(item.path.read_bytes(), client.objects[("bucket", item.key)]["body"])

    def test_replay_read_failure_closes_body_and_does_not_publish_manifest(self):
        with TemporaryDirectory() as directory:
            client = Mock()
            client.put_object.side_effect = S3Error("PreconditionFailed", 412)
            body = Mock()
            body.read.side_effect = OSError("stream interrupted")
            client.get_object.return_value = {"Body": body}
            with self.assertRaises(OSError):
                upload_landing_batch(client, "bucket", create_batch(Path(directory)), "atomic-test")
            body.close.assert_called_once()
            self.assertEqual(1, client.put_object.call_count)

    def test_checksum_prevents_publishing_a_file_changed_after_validation(self):
        class MutatingS3(FakeS3):
            def put_object(self, **kwargs):
                Path(kwargs["Body"].name).write_bytes(b"changed during upload")
                return super().put_object(**kwargs)
        with TemporaryDirectory() as directory:
            client = MutatingS3()
            with self.assertRaises(S3Error) as raised:
                upload_landing_batch(client, "bucket", create_batch(Path(directory)), "atomic-test")
            self.assertEqual("BadDigest", raised.exception.response["Error"]["Code"])
            self.assertFalse(client.objects)
