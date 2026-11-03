"""Validation of a narrowly scoped Terraform destruction plan."""

from __future__ import annotations

from typing import Any
from .analyst_access import access_addresses, validate_delete


ALLOWED_DELETE_ADDRESSES = frozenset(
    {
        "aws_glue_catalog_database.lakehouse",
        "aws_athena_workgroup.demo",
        "aws_iam_role_policy.glue_silver",
        "aws_glue_job.silver_process_indexes",
        "aws_glue_job.silver_process_reference",
        "aws_s3_object.silver_process_reference_script",
        'aws_glue_job.gold["price"]',
        'aws_glue_job.gold["aggregate"]',
        'aws_s3_object.gold_script["price"]',
        'aws_s3_object.gold_script["aggregate"]',
        'aws_s3_object.gold_script["pricing"]',
        'aws_s3_object.gold_script["adapter"]',
        'aws_s3_object.gold_script["refresh"]',
        'aws_s3_object.benchmark_script["build"]',
        'aws_s3_object.benchmark_script["profile"]',
        'aws_s3_object.benchmark_script["compaction"]',
        'aws_s3_object.benchmark_script["compaction_profile"]',
        "aws_s3_bucket.lakehouse",
        "aws_s3_object.silver_process_indexes_script",
        "aws_s3_bucket_public_access_block.lakehouse",
        "aws_s3_bucket_server_side_encryption_configuration.lakehouse",
        "random_id.bucket_suffix",
    }
)
ALLOWED_DELETE_ADDRESSES = ALLOWED_DELETE_ADDRESSES | access_addresses()

TAGGABLE_DELETE_ADDRESSES = frozenset(
    {
        "aws_glue_catalog_database.lakehouse",
        "aws_athena_workgroup.demo",
        "aws_glue_job.silver_process_indexes",
        "aws_glue_job.silver_process_reference",
        "aws_s3_object.silver_process_reference_script",
        'aws_glue_job.gold["price"]',
        'aws_glue_job.gold["aggregate"]',
        'aws_s3_object.gold_script["price"]',
        'aws_s3_object.gold_script["aggregate"]',
        'aws_s3_object.gold_script["pricing"]',
        'aws_s3_object.gold_script["adapter"]',
        'aws_s3_object.gold_script["refresh"]',
        'aws_s3_object.benchmark_script["build"]',
        'aws_s3_object.benchmark_script["profile"]',
        'aws_s3_object.benchmark_script["compaction"]',
        'aws_s3_object.benchmark_script["compaction_profile"]',
        "aws_s3_bucket.lakehouse",
    }
)
TAGGABLE_DELETE_ADDRESSES = TAGGABLE_DELETE_ADDRESSES | {
    "aws_iam_role.analyst[0]", "aws_iam_role.lakeformation_data[0]", "aws_athena_workgroup.analyst[0]"}


class DestroyPlanRejected(ValueError):
    """Raised when a destruction plan exceeds the approved demonstration scope."""


def _tags(before: dict[str, Any]) -> dict[str, str]:
    return before.get("tags_all") or before.get("tags") or {}


def validate_destroy_plan(plan: dict[str, Any], demo_id: str, expires_at: str) -> None:
    """Reject plans that leave the approved demo deletion scope.

    A previous cleanup can have removed a subset of data-plane resources before
    an AWS-side failure. Accepting another safe subset makes cleanup idempotent
    while the explicit allowlist keeps it fail-closed.
    """

    changes = plan.get("resource_changes")
    # Terraform JSON 1.2 omits this key for a genuine no-op. A targeted
    # destroy plan may have complete=false without being errored/applyable.
    if ("resource_changes" not in plan and plan.get("format_version") == "1.2"
            and plan.get("applyable") is False and plan.get("errored") is False
            and isinstance(plan.get("complete"), bool)):
        changes = []
    if not isinstance(changes, list):
        raise DestroyPlanRejected("Terraform plan has no resource_changes list.")

    delete_changes = []
    for change in changes:
        address = change.get("address")
        actions = change.get("change", {}).get("actions")
        if actions == ["no-op"]:
            continue
        if actions != ["delete"]:
            raise DestroyPlanRejected(
                f"{address!r} has disallowed actions {actions!r}; only deletion is permitted."
            )
        if address not in ALLOWED_DELETE_ADDRESSES:
            raise DestroyPlanRejected(f"{address!r} is outside the approved cleanup scope.")
        delete_changes.append(change)

    deleted_addresses = {change["address"] for change in delete_changes}
    if not deleted_addresses:
        return

    for change in delete_changes:
        address = change["address"]
        if address in access_addresses():
            try:
                validate_delete(address, change.get("change", {}).get("before") or {}, demo_id)
            except ValueError as error:
                raise DestroyPlanRejected(str(error)) from error
        if address == "aws_iam_role_policy.glue_silver":
            before = change.get("change", {}).get("before") or {}
            expected = "aws-lakehouse-platform-demo-glue-silver"
            if before.get("role") != expected or before.get("name") != expected:
                raise DestroyPlanRejected("Only the exact Glue execution inline policy may be deleted.")
        if address not in TAGGABLE_DELETE_ADDRESSES:
            continue
        tags = _tags(change.get("change", {}).get("before") or {})
        expected = {
            "Project": "aws-lakehouse-platform",
            "Environment": "demo",
            "DemoId": demo_id,
            "AutoCleanup": "true",
            "ExpiresAt": expires_at,
        }
        actual = {key: tags.get(key) for key in expected}
        if actual != expected:
            raise DestroyPlanRejected(
                f"{address!r} does not have the required project and demo tags: {actual!r}."
            )
