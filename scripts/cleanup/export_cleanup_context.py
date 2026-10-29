#!/usr/bin/env python3
"""Print validated cleanup context as Bash exports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex

from lakehouse_platform.cleanup_context import CleanupContextRejected, derive_cleanup_context


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()

    try:
        context = derive_cleanup_context(json.loads(args.state.read_text(encoding="utf-8")))
    except (CleanupContextRejected, OSError, json.JSONDecodeError) as error:
        print(f"Cleanup context rejected: {error}")
        return 1

    print(f"export PROJECT_NAME={shlex.quote(context.project)}")
    print(f"export DEMO_ID={shlex.quote(context.demo_id)}")
    print(f"export DEMO_EXPIRES_AT={shlex.quote(context.expires_at)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
