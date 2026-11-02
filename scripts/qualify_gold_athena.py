#!/usr/bin/env python3
"""Compare all three Athena Gold products to complete local batch history."""

import argparse
import json
from pathlib import Path

from lakehouse_platform.glue_runner import build_gold_arguments
from lakehouse_platform.qualification import qualify_gold


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dirs", nargs="+", type=Path)
    parser.add_argument("--database", required=True)
    parser.add_argument("--workgroup", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--region", default="eu-west-3")
    parser.add_argument("--output", type=Path, required=True, help="Local summary report without account identifiers")
    args = parser.parse_args()
    build_gold_arguments(args.database)
    if any(not directory.is_dir() for directory in args.batch_dirs):
        parser.error("Every batch directory must exist")
    import boto3

    athena = boto3.Session(profile_name=args.profile, region_name=args.region).client("athena")
    report = qualify_gold(athena, args.database, args.workgroup, args.batch_dirs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
