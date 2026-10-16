#!/usr/bin/env python3
"""Generate one deterministic late or corrected cumulative-index event."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from lakehouse_platform.late_events import generate_exception_event


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source-batch-dir", type=Path, required=True)
parser.add_argument("--kind", choices=("late", "correction"), required=True)
parser.add_argument("--output-dir", type=Path, default=Path("artifacts/exception"))
parser.add_argument("--received-at", default="2026-10-20T00:00:00Z")
parser.add_argument("--seed", type=int, default=20261018)


def main() -> None:
    args = parser.parse_args()
    received_at = datetime.fromisoformat(args.received_at.replace("Z", "+00:00")).astimezone(UTC)
    generate_exception_event(args.source_batch_dir, args.output_dir, args.kind, received_at, args.seed)
    print(f"Generated one {args.kind} consumption-index event in {args.output_dir}")


if __name__ == "__main__":
    main()
