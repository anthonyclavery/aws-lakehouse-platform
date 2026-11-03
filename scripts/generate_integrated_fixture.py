#!/usr/bin/env python3
"""Generate source batches for the complete managed identity-chain qualification."""
import argparse
import json
from pathlib import Path
from lakehouse_platform.integrated_fixture import write_integrated_fixture

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_integrated_fixture(args.output_dir), indent=2))
