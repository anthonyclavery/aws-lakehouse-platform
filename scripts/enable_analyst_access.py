#!/usr/bin/env python3
"""Enable only project analyst resources, seal legacy Gold grants, restore LF bootstrap."""
import argparse
import json
from pathlib import Path
import subprocess

from lakehouse_platform.analyst_access import access_addresses, seal_gold, temporary_administrator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--var-file", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--governance-profile", required=True)
    parser.add_argument("--project-profile", required=True)
    parser.add_argument("--region", default="eu-west-3")
    args = parser.parse_args()
    args.artifact_dir.mkdir(parents=True, exist_ok=False)
    import boto3
    operator, admin = [boto3.Session(profile_name=p, region_name=args.region) for p in (args.project_profile, args.governance_profile)]
    identity, project = admin.client("sts").get_caller_identity(), operator.client("sts").get_caller_identity()
    if identity["Account"] != project["Account"] or "terraform-operator" not in project["Arn"]:
        raise ValueError("Require the same-account project operator and governance profiles")
    principal = identity["Arn"]
    if ":assumed-role/" in principal:
        principal = admin.client("iam").get_role(RoleName=principal.split("/")[1])["Role"]["Arn"]
    def execute(command, name):
        result = subprocess.run(command, capture_output=True, text=True)
        (args.artifact_dir / name).write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"Operation failed; inspect private {name}")
        return result.stdout
    plan = args.artifact_dir.resolve() / "analyst.tfplan"
    execute(["terraform", "-chdir=terraform", "plan", "-input=false", "-no-color", f"-var-file={args.var_file.resolve()}",
             "-var=analyst_access_enabled=true", f"-var=analyst_governance_profile={args.governance_profile}", f"-out={plan}"], "plan.log")
    data = json.loads(execute(["terraform", "-chdir=terraform", "show", "-json", str(plan)], "plan.json"))
    changes = [r for r in data.get("resource_changes", []) if r["change"]["actions"] != ["no-op"] and r["mode"] == "managed"]
    if any(r["address"] not in access_addresses() or r["change"]["actions"] not in (["create"], ["update"]) for r in changes):
        raise ValueError("Enable plan includes mutations outside the exact analyst resource set")
    print(f"Reviewed {len(changes)} project-scoped analyst changes", flush=True)
    record = None
    try:
        with temporary_administrator(admin.client("lakeformation"), principal) as record:
            (args.artifact_dir / "lf-settings-before.json").write_text(json.dumps(record["before"], indent=2))
            execute(["terraform", "-chdir=terraform", "apply", "-input=false", "-no-color", str(plan)], "apply.log")
            outputs = json.loads(execute(["terraform", "-chdir=terraform", "output", "-json"], "outputs.json"))
            access = outputs["analyst_access"]["value"]
            seal_gold(admin.client("lakeformation"), identity["Account"], outputs["glue_database_name"]["value"],
                      access["role_arn"], f"arn:aws:iam::{identity['Account']}:role/aws-lakehouse-platform-demo-glue-silver",
                      principal, f"arn:aws:iam::{identity['Account']}:role/aws-lakehouse-platform-demo-cleanup-build")
    finally:
        if record is not None:
            (args.artifact_dir / "lf-bootstrap-journal.json").write_text(json.dumps(record, indent=2))
    print("Analyst access enabled, legacy Gold grants removed, LF bootstrap settings restored", flush=True)


if __name__ == "__main__":
    main()
