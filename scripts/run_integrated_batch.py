#!/usr/bin/env python3
"""Run a source batch through dedicated ingestion and the normal managed jobs."""
import argparse
import json
from pathlib import Path
from lakehouse_platform.ingestion_access import assume_ingestion_session
from lakehouse_platform.integrated_pipeline import build_integrated_invocations, run_integrated_batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--project-profile", required=True)
    parser.add_argument("--region", default="eu-west-3")
    parser.add_argument("--load-references", action="store_true", help="Load all four references before indexes (initial batch only)")
    parser.add_argument("--artifact-dir", type=Path, required=True, help="Fresh private directory for raw job journal and sanitized result")
    args = parser.parse_args()
    values = {k: v["value"] for k, v in json.loads(args.outputs.read_text()).items()}
    stages = build_integrated_invocations(values, args.batch_id, load_references=args.load_references)
    args.artifact_dir.mkdir(parents=True, exist_ok=False)
    import boto3
    operator = boto3.Session(profile_name=args.project_profile, region_name=args.region)
    ingestion = assume_ingestion_session(operator, values["ingestion_access"]["role_arn"], values["landing_path"].split("/")[2])
    role = values["ingestion_access"]["role_arn"]
    actual = ingestion.client("sts").get_caller_identity()["Arn"]
    if not actual.startswith(role.replace(":iam:", ":sts:").replace(":role/", ":assumed-role/") + "/"):
        raise ValueError("Source ingestion must actually use the expected temporary role")
    glue = operator.client("glue")
    account = operator.client("sts").get_caller_identity()["Account"]
    writer = f"arn:aws:iam::{account}:role/aws-lakehouse-platform-demo-glue-silver"
    for job in {job for _, job, _ in stages}:
        if glue.get_job(JobName=job)["Job"]["Role"] != writer:
            raise ValueError("Every managed job must run as the dedicated transformation role")
    report = run_integrated_batch(ingestion.client("s3"), glue, values, args.batch_dir, args.batch_id,
                                  load_references=args.load_references, journal=args.artifact_dir / "jobs.jsonl")
    report.update(actual_ingestion_role_verified=True, transformation_execution_role_verified=True)
    (args.artifact_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Integrated source ingestion and managed Silver/Gold stages succeeded")


if __name__ == "__main__":
    main()
