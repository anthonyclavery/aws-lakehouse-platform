#!/usr/bin/env python3
"""Build and measure an isolated Iceberg pair; publish only a sanitized report."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from lakehouse_platform.glue_runner import run_glue_jobs
from lakehouse_platform.partition_benchmark import Profile, build_invocation, measure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--workgroup", required=True)
    parser.add_argument("--glue-job", required=True)
    parser.add_argument("--script-uri", required=True)
    parser.add_argument("--module-uri", required=True)
    parser.add_argument("--benchmark-id", required=True)
    parser.add_argument("--history-days", type=int, default=730)
    parser.add_argument("--meters", type=int, default=200)
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--query-date")
    parser.add_argument("--repeats", type=int, choices=range(3, 11), default=3)
    parser.add_argument("--profile")
    parser.add_argument("--region", default="eu-west-3")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--measure-only", action="store_true", help="Measure an already successful build without creating tables")
    args = parser.parse_args()
    profile = Profile(args.benchmark_id, args.history_days, args.meters, args.start_date)
    profile.window(args.query_date)
    invocation = build_invocation(args.database, args.glue_job, args.script_uri, args.module_uri, profile)
    if args.output.exists():
        raise FileExistsError("Refusing to overwrite an existing benchmark report")
    if args.dry_run:
        print(json.dumps({"job": args.glue_job, "arguments": invocation, "expected_result": profile.expected(args.query_date),
                          "measurement_queries": 2 * args.repeats, "metadata_queries": 4}, indent=2))
        return
    import boto3
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    if not args.measure_only:
        run_glue_jobs(session.client("glue"), [(args.glue_job, invocation)])
    uri = urlsplit(invocation["MANIFEST_URI"])
    manifest = json.loads(session.client("s3").get_object(Bucket=uri.netloc, Key=uri.path.lstrip("/"))["Body"].read())
    report = measure(session.client("athena"), args.database, args.workgroup, profile, manifest,
                     repeats=args.repeats, query_date=args.query_date)
    report["region"] = args.region
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print(f"Validated benchmark report: {args.output}")


if __name__ == "__main__":
    main()
