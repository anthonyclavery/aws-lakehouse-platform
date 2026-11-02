#!/usr/bin/env python3
"""Run the Gold pricing/aggregation chain; --dry-run displays arguments offline."""

import argparse
import json

from lakehouse_platform.glue_runner import build_gold_arguments, run_gold_jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pricing-job", required=True)
    parser.add_argument("--aggregate-job", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--region", default="eu-west-3")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    arguments = build_gold_arguments(args.database)
    if args.dry_run:
        print(json.dumps(dict(zip((args.pricing_job, args.aggregate_job), arguments)), indent=2))
        return
    import boto3

    glue = boto3.Session(profile_name=args.profile, region_name=args.region).client("glue")
    run_gold_jobs(glue, args.pricing_job, args.aggregate_job, args.database)


if __name__ == "__main__":
    main()
