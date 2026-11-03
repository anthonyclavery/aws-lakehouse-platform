#!/usr/bin/env python3
"""Create deterministic source batches for governed late/correction/replay tests."""
import argparse
import json
from pathlib import Path
from lakehouse_platform.late_chain_fixture import write_late_chain_fixture

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_late_chain_fixture(args.output_dir), indent=2))
