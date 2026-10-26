#!/usr/bin/env python3
"""Validate a Terraform destroy plan before the controlled cleanup applies it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lakehouse_platform.cleanup_guard import DestroyPlanRejected, validate_destroy_plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--demo-id", required=True)
    parser.add_argument("--expires-at", required=True)
    args = parser.parse_args()

    try:
        validate_destroy_plan(
            json.loads(args.plan.read_text(encoding="utf-8")),
            demo_id=args.demo_id,
            expires_at=args.expires_at,
        )
    except (DestroyPlanRejected, OSError, json.JSONDecodeError) as error:
        print(f"Cleanup plan rejected: {error}")
        return 1

    print("Cleanup plan accepted: only the approved demo resources will be destroyed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
