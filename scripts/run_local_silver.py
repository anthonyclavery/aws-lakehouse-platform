#!/usr/bin/env python3
"""Run the local reference processor over one or more batch directories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lakehouse_platform.processing import process_indexes


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("batch_dirs", nargs="+", type=Path)
parser.add_argument("--output-dir", type=Path, default=Path("artifacts/silver"))


def main() -> None:
    args = parser.parse_args()
    result = process_indexes(args.batch_dirs)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in result.items():
        with (args.output_dir / f"{name}.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"Derived {len(result['consumption_intervals'])} intervals and quarantined {len(result['quarantine'])} indexes")


if __name__ == "__main__":
    main()
