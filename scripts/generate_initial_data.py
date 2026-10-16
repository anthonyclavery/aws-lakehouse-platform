#!/usr/bin/env python3
"""Generate a deterministic, synthetic initial batch for local validation."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from lakehouse_platform.generator import GeneratorConfig, write_initial_batch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--customers", type=int, default=100)
    parser.add_argument("--sites", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--as-of", default="2026-10-16T00:00:00Z", help="UTC ISO-8601 timestamp")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/initial"))
    parser.add_argument("--without-indexes", action="store_true", help="Write reference flows only")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")).astimezone(UTC)
    manifest = write_initial_batch(GeneratorConfig(
        customers=args.customers, sites=args.sites, seed=args.seed, as_of=as_of,
        output_dir=args.output_dir, include_indexes=not args.without_indexes,
    ))
    print(f"Generated {sum(manifest['record_counts'].values())} synthetic records in {args.output_dir}")


if __name__ == "__main__":
    main()
