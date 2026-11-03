#!/usr/bin/env python3
"""Prove conditional Bronze writes on a tagged disposable S3 demonstration."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
from threading import Barrier, Lock

from lakehouse_platform.landing import ImmutableObjectConflict, plan_landing_upload, upload_landing_batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", default="eu-west-3")
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--batch-prefix", required=True, help="Fresh unique synthetic qualification ID, at most 48 characters")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,47}", args.batch_prefix) or not args.bucket.startswith("aws-lakehouse-platform-"):
        raise ValueError("Require a project bucket and a safe fresh qualification prefix")
    if args.report.exists():
        raise ValueError("The report path must be fresh")
    import boto3
    from botocore.exceptions import ClientError
    s3 = boto3.Session(profile_name=args.profile, region_name=args.region).client("s3")
    tags = {r["Key"]: r["Value"] for r in s3.get_bucket_tagging(Bucket=args.bucket)["TagSet"]}
    if any(tags.get(k) != v for k, v in {"Project": "aws-lakehouse-platform", "Environment": "demo", "AutoCleanup": "true"}.items()):
        raise ValueError("Qualification may write only to a tagged disposable project bucket")
    cases = {name: args.batch_prefix + "-" + name for name in ("race", "missing", "spoof", "digest")}
    plans = {name: plan_landing_upload(args.batch_dir, batch_id) for name, batch_id in cases.items()}
    for batch_id in cases.values():
        if s3.list_objects_v2(Bucket=args.bucket, Prefix=f"landing/batch_id={batch_id}/", MaxKeys=1).get("KeyCount") != 0:
            raise ValueError("Qualification refuses any pre-existing case prefix")

    def read(key):
        body = s3.get_object(Bucket=args.bucket, Key=key)["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def absent(key):
        try:
            s3.head_object(Bucket=args.bucket, Key=key)
        except ClientError as error:
            if error.response["Error"]["Code"] not in {"404", "NoSuchKey", "NotFound"} or error.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                raise
        else:
            raise AssertionError("A rejected upload published an object")

    class RacingClient:
        barrier = Barrier(2)
        lock = Lock()
        precondition_failures = 0

        def get_object(self, **kwargs):
            return s3.get_object(**kwargs)

        def put_object(self, **kwargs):
            if kwargs["Key"] == plans["race"][0].key:
                self.barrier.wait(timeout=30)
            try:
                return s3.put_object(**kwargs)
            except ClientError as error:
                if error.response["Error"]["Code"] == "PreconditionFailed" and error.response["ResponseMetadata"]["HTTPStatusCode"] == 412:
                    with self.lock:
                        self.precondition_failures += 1
                raise

    client = RacingClient()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: upload_landing_batch(client, args.bucket, args.batch_dir, cases["race"]), range(2)))
    count = len(plans["race"])
    assert sum(len(r.uploaded_keys) for r in results) == count
    assert sum(len(r.existing_keys) for r in results) == count
    assert client.precondition_failures == count
    assert all(hashlib.sha256(read(item.key)).hexdigest() == item.sha256 for item in plans["race"])
    replay = upload_landing_batch(s3, args.bucket, args.batch_dir, cases["race"])
    assert not replay.uploaded_keys and len(replay.existing_keys) == count
    conflicts = {}
    for name in ("missing", "spoof"):
        first = plans[name][0]
        original = b"different synthetic content\n"
        metadata = {} if name == "missing" else {"sha256": first.sha256}
        s3.put_object(Bucket=args.bucket, Key=first.key, Body=original, IfNoneMatch="*", Metadata=metadata)
        try:
            upload_landing_batch(s3, args.bucket, args.batch_dir, cases[name])
        except ImmutableObjectConflict:
            pass
        else:
            raise AssertionError("Conflicting bytes were accepted as an immutable replay")
        assert read(first.key) == original
        absent(plans[name][-1].key)
        conflicts[name] = {"conflict_rejected": True, "original_preserved": True, "manifest_absent": True}
    key = plans["digest"][0].key
    try:
        s3.put_object(Bucket=args.bucket, Key=key, Body=b"synthetic checksum probe", IfNoneMatch="*",
                      ChecksumSHA256=base64.b64encode(bytes(32)).decode("ascii"))
    except ClientError as error:
        assert error.response["Error"]["Code"] == "BadDigest" and error.response["ResponseMetadata"]["HTTPStatusCode"] == 400
    else:
        raise AssertionError("S3 accepted an invalid body checksum")
    absent(key)
    report = {"passed": True, "region": args.region,
              "concurrent_writers": 2, "objects_in_complete_batch": count,
              "objects_created_once": count, "native_precondition_failures_412": client.precondition_failures,
              "all_stored_bytes_match": True, "unchanged_replay_reused_all_objects": True,
              "conflicting_objects": conflicts, "native_bad_digest_400_rejected_without_object": True}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x") as output:
        output.write(json.dumps(report, indent=2) + "\n")
    print("Native concurrent writes, immutable conflicts, byte-level replay and checksum rejection passed")


if __name__ == "__main__":
    main()
