#!/usr/bin/env python3
"""Generate the complete V1 history and its streaming local acceptance oracle."""
import argparse
from pathlib import Path
from lakehouse_platform.domain_fixture import write_domain_fixture

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    write_domain_fixture(args.output_dir)
