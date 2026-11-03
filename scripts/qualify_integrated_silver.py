#!/usr/bin/env python3
"""Audit complete Silver rows, empty quarantine and finished Silver/Gold work."""
import argparse
import json
from pathlib import Path
from lakehouse_platform.integrated_validation import qualify_integrated_silver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batches", nargs="+", type=Path)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--project-profile", required=True)
    parser.add_argument("--region", default="eu-west-3")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists(): raise FileExistsError("Report path must be fresh")
    import boto3
    values = {k: v["value"] for k, v in json.loads(args.outputs.read_text()).items()}
    client = boto3.Session(profile_name=args.project_profile, region_name=args.region).client("athena")
    report = qualify_integrated_silver(client, values["glue_database_name"], values["athena_workgroup_name"], args.batches)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x") as output: output.write(json.dumps(report, indent=2) + "\n")
    if not report["passed"]: raise SystemExit("Silver reconciliation or completed-work checks failed")
    print("Complete Silver reference/index/interval comparison and finished-work checks passed")


if __name__ == "__main__":
    main()
