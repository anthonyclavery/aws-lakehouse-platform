#!/usr/bin/env python3
"""Rebuild local Silver and Gold from complete initial/incremental batch history."""

import argparse
import json
from pathlib import Path

from lakehouse_platform.pricing import gold_products, price_intervals
from lakehouse_platform.processing import process_indexes
from lakehouse_platform.reference_processing import process_reference_data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_dirs", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/gold"))
    args = parser.parse_args()
    for directory in args.batch_dirs:
        if not directory.is_dir():
            parser.error(f"Batch directory does not exist: {directory}")
    silver = process_indexes(args.batch_dirs)
    references = process_reference_data(args.batch_dirs)
    segments = price_intervals(silver["consumption_intervals"], references)
    products = {"priced_intervals": segments, **gold_products(segments)}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in products.items():
        path = args.output_dir / f"{name}.jsonl"
        temporary = path.with_suffix(".tmp")
        temporary.write_text("".join(json.dumps(row, default=str, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
        temporary.replace(path)
        print(f"{name}: {len(rows)} rows")
    print(f"Excluded {len(silver['quarantine'])} quarantined indexes")


if __name__ == "__main__":
    main()
