"""Explicit build/compact phases for one disposable benchmark table."""
import json
import sys
from urllib.parse import urlsplit


def main():
    import boto3
    from botocore.exceptions import ClientError
    from awsglue.context import GlueContext
    from awsglue.utils import getResolvedOptions
    from pyspark.context import SparkContext
    from compaction_benchmark import CompactionProfile, build_fragmented, compact, public_manifest, validate_manifest
    from partition_benchmark import table_details
    args = getResolvedOptions(sys.argv, ["DATABASE", "PHASE", "BENCHMARK_ID", "HISTORY_DAYS", "METERS", "START_DATE",
                                         "MICRO_BATCHES", "MANIFEST_URI", "BUILD_MANIFEST_URI"])
    profile = CompactionProfile(args["BENCHMARK_ID"], int(args["HISTORY_DAYS"]), int(args["METERS"]),
                                args["START_DATE"], int(args["MICRO_BATCHES"]))
    phase = args["PHASE"]
    if phase not in ("build", "compact"):
        raise ValueError("Unknown benchmark phase")
    uri, source = urlsplit(args["MANIFEST_URI"]), urlsplit(args["BUILD_MANIFEST_URI"])
    for value, name in ((uri, phase), (source, "build")):
        if (value.scheme != "s3" or not value.netloc or value.query or value.fragment or
                value.path != f"/landing/batch_id=compaction-{profile.benchmark_id}/{name}-manifest.json"):
            raise ValueError("Manifest must use its exact isolated compaction path")
    if uri.netloc != source.netloc:
        raise ValueError("Manifests must share the deployment bucket")
    s3 = boto3.client("s3")
    def read(value):
        try:
            return json.loads(s3.get_object(Bucket=value.netloc, Key=value.path.lstrip("/"))["Body"].read())
        except ClientError as error:
            if error.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise
    existing = read(uri)
    spark = GlueContext(SparkContext.getOrCreate()).spark_session
    if phase == "build":
        if existing:
            raise ValueError("Completed build manifest exists; use resume")
        result = build_fragmented(spark, args["DATABASE"], profile)
    else:
        before = read(source)
        if not before:
            raise ValueError("A completed build manifest is required before compaction")
        validate_manifest(before, profile, "build")
        if existing:
            validate_manifest(existing, profile, "compact")
            _, current = table_details(spark, f"glue_catalog.{args['DATABASE']}.{profile.tables['compaction']}")
            if current != existing["source_snapshot"] or existing["before_snapshot"] != before["source_snapshot"]:
                raise ValueError("Completed compaction manifest is stale")
            print("COMPACTION_BENCHMARK " + json.dumps(public_manifest(existing)), flush=True)
            return
        fail = ("--FAIL_AFTER_REWRITE" in sys.argv and
                getResolvedOptions(sys.argv, ["FAIL_AFTER_REWRITE"])["FAIL_AFTER_REWRITE"] == "true")
        result = compact(spark, args["DATABASE"], profile, before, fail_after_rewrite=fail)
    # Single writer across this experiment; never overwrite a completed manifest.
    s3.put_object(Bucket=uri.netloc, Key=uri.path.lstrip("/"), Body=json.dumps(result).encode())
    print("COMPACTION_BENCHMARK " + json.dumps(public_manifest(result)), flush=True)


if __name__ == "__main__":
    main()
