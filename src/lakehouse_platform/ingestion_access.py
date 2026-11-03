"""Dedicated temporary Bronze identity and exact cleanup boundaries."""
import re

INGESTION_ADDRESSES = frozenset({"aws_iam_role.ingestion[0]", "aws_iam_role_policy.ingestion[0]"})


def validate_ingestion_delete(address, before, demo_id):
    expected = f"aws-lakehouse-platform-{demo_id}-ingestion"
    if address not in INGESTION_ADDRESSES or before.get("name") != expected:
        raise ValueError("Only this demo's exact ingestion identity may be deleted")
    if address.startswith("aws_iam_role_policy.") and before.get("role") != expected:
        raise ValueError("Only the ingestion role's exact inline policy may be deleted")


def validate_ingestion_identity(identity, role_arn, bucket):
    account = identity["Account"]
    match = re.fullmatch(r"aws-lakehouse-platform-([a-z0-9-]{3,32})-[a-f0-9]{8}", bucket)
    if not match or not re.fullmatch(r"\d{12}", account):
        raise ValueError("Require an isolated project bucket and account")
    expected = f"arn:aws:iam::{account}:role/aws-lakehouse-platform-{match[1]}-ingestion"
    if role_arn != expected or not identity["Arn"].startswith(f"arn:aws:sts::{account}:assumed-role/aws-lakehouse-platform-demo-terraform-operator/"):
        raise ValueError("Assume only this demo's ingestion role through the same-account project operator")


def assume_ingestion_session(operator, role_arn, bucket, *, session_factory=None):
    identity = operator.client("sts").get_caller_identity()
    validate_ingestion_identity(identity, role_arn, bucket)
    credentials = operator.client("sts").assume_role(RoleArn=role_arn, RoleSessionName="lakehouse-ingestion", DurationSeconds=900)["Credentials"]
    if session_factory is None:
        import boto3
        session_factory = boto3.Session
    parameters = {"aws_access_key_id": credentials["AccessKeyId"], "aws_secret_access_key": credentials["SecretAccessKey"],
                  "aws_session_token": credentials["SessionToken"], "region_name": operator.region_name}
    return session_factory(**parameters)


def expect_access_denied(operation, *, statuses=(403,)):
    try:
        response = operation()
    except Exception as error:
        response = getattr(error, "response", {})
        if (not isinstance(response, dict) or response.get("Error", {}).get("Code") not in {"AccessDenied", "AccessDeniedException"}
                or response.get("ResponseMetadata", {}).get("HTTPStatusCode") not in statuses):
            raise
        return {"denied": True, "code": response["Error"]["Code"], "http_status": response["ResponseMetadata"]["HTTPStatusCode"]}
    if isinstance(response, dict) and "Body" in response:
        response["Body"].close()
    raise AssertionError("An ingestion operation outside the allowed boundary succeeded")
