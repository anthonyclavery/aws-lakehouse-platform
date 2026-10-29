import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lakehouse_platform.landing import ImmutableObjectConflict, LandingBatchRejected, plan_landing_upload, upload_landing_batch


class NotFoundError(Exception):
    response = {"Error": {"Code": "404"}}


class FakeS3:
    def __init__(self):
        self.objects = {}

    def head_object(self, *, Bucket, Key):
        try:
            return {"Metadata": self.objects[(Bucket, Key)]["metadata"]}
        except KeyError as error:
            raise NotFoundError() from error

    def upload_file(self, Filename, Bucket, Key, ExtraArgs):
        self.objects[(Bucket, Key)] = {
            "body": Path(Filename).read_bytes(),
            "metadata": ExtraArgs["Metadata"],
        }


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
            client.objects[("bucket", "landing/batch_id=initial-20261016/reference/site_meter.jsonl")]["metadata"]["sha256"] = "different"
            with self.assertRaises(ImmutableObjectConflict):
                upload_landing_batch(client, "bucket", batch, "initial-20261016")
