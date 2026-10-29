#!/usr/bin/env python3
"""Upload one verified synthetic batch to the immutable Bronze landing prefix."""

from __future__ import annotations

import argparse
from pathlib import Path

from lakehouse_platform.landing import plan_landing_upload, upload_landing_batch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--batch-id", required=True, help="Immutable, lowercase batch identifier.")
    parser.add_argument("--bucket", required=True, help="Existing project S3 bucket name.")
    parser.add_argument("--profile", help="Optional AWS CLI profile name.")
    parser.add_argument("--region", help="Optional AWS region, for example eu-west-3.")
    parser.add_argument("--dry-run", action="store_true", help="Validate and list object keys without contacting AWS.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.dry_run:
        for item in plan_landing_upload(args.batch_dir, args.batch_id):
            print(item.key)
        return

    try:
        import boto3
    except ImportError as error:
        raise SystemExit("Install the optional AWS dependency first: python -m pip install -e '.[aws]'") from error

    client = boto3.Session(profile_name=args.profile, region_name=args.region).client("s3")
    result = upload_landing_batch(client, args.bucket, args.batch_dir, args.batch_id)
    print(f"Uploaded {len(result.uploaded_keys)} objects; reused {len(result.existing_keys)} immutable objects.")


if __name__ == "__main__":
    main()
