#!/usr/bin/env python3
"""Load selected reference flows from one immutable Bronze batch, sequentially."""

import argparse
import json

from lakehouse_platform.glue_runner import build_reference_arguments, run_reference_jobs
from lakehouse_platform.reference_processing import BUSINESS_KEYS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--batch-path", required=True, help="s3://BUCKET/landing/batch_id=BATCH_ID")
    parser.add_argument("--flows", nargs="+", choices=tuple(BUSINESS_KEYS), help="Default: all four flows; select only files present in a partial batch")
    parser.add_argument("--profile")
    parser.add_argument("--region", default="eu-west-3")
    parser.add_argument("--dry-run", action="store_true", help="Show validated arguments without contacting AWS")
    args = parser.parse_args()
    arguments = build_reference_arguments(args.database, args.batch_path, args.flows)
    if args.dry_run:
        print(json.dumps({"job_name": args.job_name, "runs": arguments}, indent=2))
        return
    import boto3

    glue = boto3.Session(profile_name=args.profile, region_name=args.region).client("glue")
    run_reference_jobs(glue, args.job_name, args.database, args.batch_path, args.flows)


if __name__ == "__main__":
    main()
