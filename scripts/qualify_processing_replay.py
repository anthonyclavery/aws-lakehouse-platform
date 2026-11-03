#!/usr/bin/env python3
"""Capture private-free Iceberg identities or compare fully audited replay states."""
import argparse
import json
from pathlib import Path
from lakehouse_platform.integrated_pipeline import build_integrated_invocations
from lakehouse_platform.replay_validation import capture_replay_metadata, compare_replay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--outputs", type=Path, required=True)
    capture.add_argument("--project-profile", required=True)
    capture.add_argument("--region", default="eu-west-3")
    compare = commands.add_parser("compare")
    for name in ("silver-before", "silver-after", "analyst-before", "analyst-after", "metadata-before", "metadata-after"):
        compare.add_argument("--" + name, type=Path, required=True)
    for command in (capture, compare): command.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists(): raise FileExistsError("Refusing to overwrite replay evidence")
    read = lambda path: json.loads(path.read_text())
    if args.mode == "capture":
        import boto3
        values = {k: v["value"] for k, v in read(args.outputs).items()}
        build_integrated_invocations(values, "replay-audit", load_references=False)
        client = boto3.Session(profile_name=args.project_profile, region_name=args.region).client("glue")
        report = capture_replay_metadata(client, values["glue_database_name"], values["landing_path"].split("/")[2])
    else:
        report = compare_replay(*(read(getattr(args, name)) for name in
            ("silver_before", "silver_after", "analyst_before", "analyst_after", "metadata_before", "metadata_after")))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x") as output: output.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.mode == "compare" and not report["passed"]: raise SystemExit("Replay content, versions or data-table metadata changed")
    print("Replay metadata captured" if args.mode == "capture" else "Full replay content, versions and data-table metadata unchanged")


if __name__ == "__main__":
    main()
