#!/usr/bin/env python3
"""Execute at most two fully validated, exact-target deletion passes."""
import argparse
import json
from lakehouse_platform.guarded_destroy import destroy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--demo-id", required=True)
    parser.add_argument("--expires-at", required=True)
    args = parser.parse_args()
    print("GUARDED_DESTROY " + json.dumps(destroy(args.region, args.demo_id, args.expires_at)), flush=True)


if __name__ == "__main__":
    main()
