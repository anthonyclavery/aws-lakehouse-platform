#!/usr/bin/env python3
"""Create fresh synthetic batches for bounded Silver and controlled retry tests."""

import argparse
import json
from pathlib import Path

from lakehouse_platform.bounded_fixture import write_bounded_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,default=Path("artifacts/bounded-silver-fixture"))
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("Use a fresh output directory; existing fixtures are preserved")
    print(json.dumps(write_bounded_fixture(args.output_dir),indent=2))


if __name__ == "__main__":
    main()
