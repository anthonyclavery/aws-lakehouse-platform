"""Build an isolated, deterministic pair of Iceberg benchmark tables on Glue."""
import json
import sys
from urllib.parse import urlsplit


def main():
    from awsglue.context import GlueContext
    from awsglue.utils import getResolvedOptions
    from pyspark.context import SparkContext
    import boto3
    from partition_benchmark import Profile, build_tables
    args = getResolvedOptions(sys.argv, ["DATABASE", "BENCHMARK_ID", "HISTORY_DAYS", "METERS", "START_DATE", "MANIFEST_URI"])
    profile = Profile(args["BENCHMARK_ID"], int(args["HISTORY_DAYS"]), int(args["METERS"]), args["START_DATE"])
    uri = urlsplit(args["MANIFEST_URI"])
    if (uri.scheme != "s3" or not uri.netloc or uri.query or uri.fragment or
            uri.path != f"/landing/batch_id=benchmark-{profile.benchmark_id}/benchmark-manifest.json"):
        raise ValueError("Manifest must use the exact isolated benchmark landing path")
    spark = GlueContext(SparkContext.getOrCreate()).spark_session
    result = build_tables(spark, args["DATABASE"], profile)
    boto3.client("s3").put_object(Bucket=uri.netloc, Key=uri.path.lstrip("/"), Body=json.dumps(result).encode())
    print("PARTITION_BENCHMARK_BUILD " + json.dumps({k: v for k, v in result.items() if k != "source_snapshots"}), flush=True)


if __name__ == "__main__":
    main()
