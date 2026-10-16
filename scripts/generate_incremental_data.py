#!/usr/bin/env python3
"""Continue a generated initial batch with a deterministic incremental batch."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from lakehouse_platform.incremental import generate_incremental_batch


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--initial-batch-dir", type=Path, required=True)
parser.add_argument("--output-dir", type=Path, default=Path("artifacts/incremental"))
parser.add_argument("--until", required=True, help="UTC ISO-8601 timestamp")
parser.add_argument("--seed", type=int, default=20261017)


def main() -> None:
    args = parser.parse_args()
    until = datetime.fromisoformat(args.until.replace("Z", "+00:00")).astimezone(UTC)
    manifest = generate_incremental_batch(args.initial_batch_dir, args.output_dir, until, args.seed)
    print(f"Generated {manifest['record_counts']['consumption_index']} incremental indexes in {args.output_dir}")


if __name__ == "__main__":
    main()
