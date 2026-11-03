#!/usr/bin/env python3
"""Qualify actual temporary Bronze credentials and existing-resource denials."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from lakehouse_platform.ingestion_access import assume_ingestion_session, expect_access_denied
from lakehouse_platform.landing import plan_landing_upload, upload_landing_batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", type=Path, required=True, help="Private terraform output -json file")
    parser.add_argument("--project-profile", required=True)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--batch-id", required=True, help="Fresh synthetic batch ID")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--region", default="eu-west-3")
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError("Refusing to overwrite qualification evidence")
    import boto3
    from botocore.exceptions import ClientError
    values = {k: v["value"] for k, v in json.loads(args.outputs.read_text()).items()}
    bucket, database = values["landing_path"].split("/")[2], values["glue_database_name"]
    role_arn = values["ingestion_access"]["role_arn"]
    operator = boto3.Session(profile_name=args.project_profile, region_name=args.region)
    ingestion = assume_ingestion_session(operator, role_arn, bucket)
    expected_arn = role_arn.replace(":iam:", ":sts:").replace(":role/", ":assumed-role/") + "/"
    if not ingestion.client("sts").get_caller_identity()["Arn"].startswith(expected_arn):
        raise ValueError("Qualification must actually use the expected ingestion role")
    s3, op_s3, glue = ingestion.client("s3"), operator.client("s3"), operator.client("glue")
    planned = plan_landing_upload(args.batch_dir, args.batch_id)
    prefix = f"landing/batch_id={args.batch_id}/"
    if op_s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1).get("KeyCount") != 0:
        raise ValueError("Qualification requires a fresh batch prefix")
    first = upload_landing_batch(s3, bucket, args.batch_dir, args.batch_id)
    replay = upload_landing_batch(s3, bucket, args.batch_dir, args.batch_id)
    assert len(first.uploaded_keys) == len(planned) and not first.existing_keys
    assert len(replay.existing_keys) == len(planned) and not replay.uploaded_keys
    listed = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    assert {r["Key"] for r in listed.get("Contents", [])} == {item.key for item in planned}

    def read(client, key):
        body = client.get_object(Bucket=bucket, Key=key)["Body"]
        try:
            return body.read()
        finally:
            body.close()

    assert all(hashlib.sha256(read(s3, item.key)).hexdigest() == item.sha256 for item in planned)
    denials = {}
    key = planned[0].key
    denials["unconditional_overwrite"] = expect_access_denied(lambda: s3.put_object(Bucket=bucket, Key=key, Body=b"synthetic forbidden overwrite"))
    fresh_key = prefix + "unconditional-probe.json"
    denials["unconditional_create"] = expect_access_denied(lambda: s3.put_object(Bucket=bucket, Key=fresh_key, Body=b"{}"))
    try:
        op_s3.head_object(Bucket=bucket, Key=fresh_key)
    except ClientError as error:
        assert error.response["Error"]["Code"] in {"404", "NoSuchKey"} and error.response["ResponseMetadata"]["HTTPStatusCode"] == 404
    else:
        raise AssertionError("An unconditional object was created")
    denials["delete_batch_object"] = expect_access_denied(lambda: s3.delete_object(Bucket=bucket, Key=key))
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=b"synthetic conflicting conditional overwrite", IfNoneMatch="*")
    except ClientError as error:
        assert error.response["Error"]["Code"] == "PreconditionFailed" and error.response["ResponseMetadata"]["HTTPStatusCode"] == 412
    else:
        raise AssertionError("A conditional overwrite succeeded")
    assert hashlib.sha256(read(op_s3, key)).hexdigest() == planned[0].sha256
    for label, kwargs in (("list_bucket_root", {}), ("list_landing_root", {"Prefix": "landing/"}), ("list_warehouse", {"Prefix": "warehouse/"})):
        denials[label] = expect_access_denied(lambda kwargs=kwargs: s3.list_objects_v2(Bucket=bucket, **kwargs))

    # Prove probes exist independently before counting any refused reads.
    probes = {}
    for label, table in (("silver", "meter_consumption_interval"), ("gold", "daily_site_energy")):
        location = glue.get_table(DatabaseName=database, Name=table)["Table"]["StorageDescriptor"]["Location"]
        uri = urlsplit(location)
        if uri.scheme != "s3" or uri.netloc != bucket or not uri.path.startswith("/warehouse/"):
            raise ValueError("Data probe escapes the project warehouse")
        keys = op_s3.list_objects_v2(Bucket=bucket, Prefix=uri.path.strip("/") + "/data/", MaxKeys=1000)
        probes[label] = next(r["Key"] for r in keys.get("Contents", []) if r["Key"].endswith(".parquet"))
    job_name = values["gold_job_names"]["price"]
    job = glue.get_job(JobName=job_name)["Job"]
    script = urlsplit(job["Command"]["ScriptLocation"])
    if script.scheme != "s3" or script.netloc != bucket or not script.path.startswith("/jobs/"):
        raise ValueError("Script probe escapes project scripts")
    probes["scripts"] = script.path.lstrip("/")
    for label, probe in probes.items():
        op_s3.get_object(Bucket=bucket, Key=probe, Range="bytes=0-0")["Body"].close()
        denials[label + "_read"] = expect_access_denied(lambda probe=probe: s3.get_object(Bucket=bucket, Key=probe, Range="bytes=0-0"))
        denials[label + "_write"] = expect_access_denied(lambda probe=probe: s3.put_object(Bucket=bucket, Key=probe, Body=b"{}", IfNoneMatch="*"))
    workgroup = values["athena_workgroup_name"]
    operator.client("athena").get_work_group(WorkGroup=workgroup)
    glue.get_database(Name=database)
    denials["glue_database"] = expect_access_denied(lambda: ingestion.client("glue").get_database(Name=database), statuses=(400, 403))
    denials["glue_gold_table"] = expect_access_denied(lambda: ingestion.client("glue").get_table(DatabaseName=database, Name="daily_site_energy"), statuses=(400, 403))
    denials["glue_job"] = expect_access_denied(lambda: ingestion.client("glue").get_job(JobName=job_name), statuses=(400, 403))
    denials["athena_workgroup"] = expect_access_denied(lambda: ingestion.client("athena").get_work_group(WorkGroup=workgroup), statuses=(400, 403))
    denials["lakeformation_settings"] = expect_access_denied(lambda: ingestion.client("lakeformation").get_data_lake_settings(), statuses=(400, 403))
    writer = "aws-lakehouse-platform-demo-glue-silver"
    writer_arn = operator.client("iam").get_role(RoleName=writer)["Role"]["Arn"]
    denials["iam_role_metadata"] = expect_access_denied(lambda: ingestion.client("iam").get_role(RoleName=writer))
    denials["assume_writer_role"] = expect_access_denied(lambda: ingestion.client("sts").assume_role(RoleArn=writer_arn, RoleSessionName="ingestion-denied"))
    report = {"passed": True, "region": args.region, "temporary_credentials_seconds": 900,
              "actual_ingestion_role_verified": True, "uploaded_objects": len(first.uploaded_keys),
              "replayed_objects": len(replay.existing_keys), "batch_listing_and_all_bytes_match": True,
              "conditional_overwrite_412": True, "original_batch_object_preserved": True,
              "no_unconditional_object_created": True, "outside_s3_probes_exist_for_operator": True, "denials": denials}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x") as output:
        output.write(json.dumps(report, indent=2) + "\n")
    print("Actual temporary ingestion role: Bronze upload/replay allowed; mutation, non-Bronze and control-plane access refused")


if __name__ == "__main__":
    main()
