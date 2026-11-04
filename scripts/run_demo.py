#!/usr/bin/env python3
"""Prepare, preview, deploy, run, query and clean the same synthetic demonstration."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import traceback

from lakehouse_platform.analyst_queries import QUERIES
from lakehouse_platform.demo import load_state, locked, prepare, read, run_scenarios, save_report, write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True, help="Fresh ignored private workspace, preferably under artifacts/")
    parser.add_argument("--project-profile")
    parser.add_argument("--governance-profile")
    parser.add_argument("--region", default="eu-west-3")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("prepare", help="Generate immutable source batches and SQL examples without AWS")
    setup.add_argument("--run-id", default="demo-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    commands.add_parser("local", help="Run all five scenarios offline and write a business report")
    plan = commands.add_parser("plan", help="Save a guarded Terraform plan and show its resource actions")
    plan.add_argument("--var-file", type=Path, required=True, help="Private JSON Terraform variables; initialized backend required")
    commands.add_parser("deploy", help="Apply only the unchanged saved plan after its review")
    commands.add_parser("run", help="Run initial/late/correction/replays and real analyst checks on AWS")
    query = commands.add_parser("query", help="Read a named Gold query as the dedicated analyst")
    query.add_argument("--name", choices=QUERIES, required=True)
    query.add_argument("--start-date", required=True, help="Inclusive Europe/Paris date")
    query.add_argument("--end-date", required=True, help="Exclusive Europe/Paris date")
    query.add_argument("--customer-id")
    commands.add_parser("cleanup", help="Request/poll the existing guarded native cleanup for this workspace")
    commands.add_parser("status", help="Show progress without any AWS call")
    report = commands.add_parser("report", help="Regenerate readable Markdown from the saved report without AWS")
    report.add_argument("--mode", choices=("local", "aws"), required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if args.command == "prepare":
        prepare(workspace, args.run_id)
        print("Prepared immutable batches and three Gold SQL examples. Next: local.")
        return
    load_state(workspace)
    if args.command == "status":
        state = load_state(workspace)
        print("\n".join(f"{key}: {state[key]}" for key in ("run_id", "local_status", "aws_status", "active_phase") if key in state))
        return
    if args.command == "query" and not args.project_profile:
        parser.error("Analyst queries require --project-profile; no governance profile is needed")
    if args.command not in ("local", "report", "query") and (not args.project_profile or not args.governance_profile):
        parser.error("AWS commands require --project-profile and --governance-profile")
    with locked(workspace):
        try:
            if args.command == "local":
                run_scenarios(workspace)
                print("Complete offline preview: local-report.md. AWS checks remain unmeasured.")
            elif args.command == "report":
                save_report(workspace, read(workspace / (args.mode + "-report.json")))
                print(args.mode + "-report.md regenerated from saved evidence.")
            else:
                from lakehouse_platform.demo_aws import AWSJourney
                journey = AWSJourney(workspace, Path(__file__).resolve().parents[1], args.project_profile, args.governance_profile,
                                     args.region, read_only=args.command == "query")
                if args.command == "plan": journey.plan(args.var_file.resolve())
                elif args.command == "deploy": journey.deploy()
                elif args.command == "run": journey.run()
                elif args.command == "cleanup": journey.cleanup()
                else:
                    result = journey.query(args.name, args.start_date, args.end_date, args.customer_id)
                    name = "query-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
                    write(workspace / (name + ".json"), result)
                    columns = list(result["rows"][0]) if result["rows"] else []
                    lines = [f"# Analyst query: {args.name}", "", f"Period: {args.start_date} inclusive to {args.end_date} exclusive.",
                             f"Origin: AWS analyst. Rows: {len(result['rows'])}. Scanned bytes: {result['bytes_scanned']}. Result reuse: false.", ""]
                    if columns:
                        lines += ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
                        lines += ["| " + " | ".join(str(row.get(column, "")).replace("|", "\\|").replace("\n", " ") for column in columns) + " |" for row in result["rows"]]
                    (workspace / (name + ".md")).write_text("\n".join(lines) + "\n")
                    print(f"Analyst query completed: {len(result['rows'])} rows. Saved {name}.md and JSON.")
                print("Stage completed. Use status to inspect progress.")
        except Exception:
            error = workspace / ("failure-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".log")
            error.write_text(traceback.format_exc())
            error.chmod(0o600)
            print("Demo stopped. Preserve the workspace and inspect its private failure/stage logs. No automatic job resubmission was performed.", file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
