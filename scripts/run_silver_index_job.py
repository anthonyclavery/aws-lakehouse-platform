#!/usr/bin/env python3
"""Start one explicit AWS Glue Silver consumption-index job run."""

from __future__ import annotations

import argparse
import time

from lakehouse_platform.glue_runner import SilverIndexInvocation, build_silver_index_arguments


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--landing-path", required=True)
    parser.add_argument("--silver-index-table", required=True)
    parser.add_argument("--silver-interval-table", required=True)
    parser.add_argument("--quarantine-table", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--region")
    parser.add_argument("--wait", action="store_true", help="Wait for completion and return a failing exit code on job failure.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    job_arguments = build_silver_index_arguments(SilverIndexInvocation(
        batch_id=args.batch_id,
        landing_path=args.landing_path,
        silver_index_table=args.silver_index_table,
        silver_interval_table=args.silver_interval_table,
        quarantine_table=args.quarantine_table,
    ))
    try:
        import boto3
    except ImportError as error:
        raise SystemExit("Install the optional AWS dependency first: python -m pip install -e '.[aws]'") from error
    glue = boto3.Session(profile_name=args.profile, region_name=args.region).client("glue")
    run_id = glue.start_job_run(JobName=args.job_name, Arguments={f"--{key}": value for key, value in job_arguments.items()})["JobRunId"]
    print(f"Started Glue job run {run_id} for batch {args.batch_id}.")
    if not args.wait:
        return
    terminal_states = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT", "ERROR"}
    while True:
        state = glue.get_job_run(JobName=args.job_name, RunId=run_id, PredecessorsIncluded=False)["JobRun"]["JobRunState"]
        if state in terminal_states:
            print(f"Glue job run {run_id} finished with state {state}.")
            if state != "SUCCEEDED":
                raise SystemExit(1)
            return
        time.sleep(15)


if __name__ == "__main__":
    main()
