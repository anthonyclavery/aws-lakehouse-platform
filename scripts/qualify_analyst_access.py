#!/usr/bin/env python3
"""Exercise actual analyst credentials against Gold, Silver and existing S3 probes."""
import argparse
import json
from pathlib import Path

from lakehouse_platform.analyst_access import qualify_access


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", type=Path, required=True, help="Private terraform output -json file")
    parser.add_argument("--expected", type=Path, required=True, help="Independent full Gold products as JSON")
    parser.add_argument("--bronze-probe-key", required=True)
    parser.add_argument("--project-profile", required=True)
    parser.add_argument("--governance-profile", required=True)
    parser.add_argument("--region", default="eu-west-3")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError("Refusing to overwrite qualification evidence")
    import boto3
    values = {k: v["value"] for k, v in json.loads(args.outputs.read_text()).items()}
    project, governance = [boto3.Session(profile_name=p, region_name=args.region) for p in (args.project_profile, args.governance_profile)]
    identity = project.client("sts").get_caller_identity()
    if identity["Account"] != governance.client("sts").get_caller_identity()["Account"] or "terraform-operator" not in identity["Arn"]:
        raise ValueError("Project and governance identities must belong to the same account")
    access = values["analyst_access"]
    assumed = project.client("sts").assume_role(RoleArn=access["role_arn"], RoleSessionName="lakehouse-analyst-qualification", DurationSeconds=900)["Credentials"]
    session_parameters = {"aws_access_key_id": assumed["AccessKeyId"], "aws_secret_access_key": assumed["SecretAccessKey"],
                          "aws_session_token": assumed["SessionToken"], "region_name": args.region}
    analyst = boto3.Session(**session_parameters)
    config = {**access, "database": values["glue_database_name"], "bucket": values["landing_path"].split("/")[2], "account": identity["Account"]}
    clients = lambda session: {name: session.client(name) for name in ("sts", "glue", "s3", "athena")}
    report = qualify_access(clients(project), clients(analyst), governance.client("lakeformation"), config,
                            json.loads(args.expected.read_text()), args.bronze_probe_key)
    report["region"] = args.region
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print("Gold reads, Silver/internal SQL denials, direct S3 denials and LF SELECT revocation/restoration verified")


if __name__ == "__main__":
    main()
