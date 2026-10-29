"""Validation of a narrowly scoped Terraform destruction plan."""

from __future__ import annotations

from typing import Any


ALLOWED_DELETE_ADDRESSES = frozenset(
    {
        "aws_glue_catalog_database.lakehouse",
        "aws_glue_job.silver_process_indexes",
        "aws_s3_bucket.lakehouse",
        "aws_s3_object.silver_process_indexes_script",
        "aws_s3_bucket_public_access_block.lakehouse",
        "aws_s3_bucket_server_side_encryption_configuration.lakehouse",
        "random_id.bucket_suffix",
    }
)

TAGGABLE_DELETE_ADDRESSES = frozenset(
    {
        "aws_glue_catalog_database.lakehouse",
        "aws_glue_job.silver_process_indexes",
        "aws_s3_bucket.lakehouse",
    }
)


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
