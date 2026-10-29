"""Derive the approved cleanup context from the isolated Terraform state."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


CONTEXT_RESOURCE_TYPE = "terraform_data"
CONTEXT_RESOURCE_NAME = "cleanup_context"
PROJECT_NAME = "aws-lakehouse-platform"
DEMO_ID_PATTERN = re.compile(r"^[a-z0-9-]{3,32}$")
EXPIRES_AT_PATTERN = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class CleanupContextRejected(ValueError):
    """Raised when the isolated Terraform state has no approved cleanup context."""


@dataclass(frozen=True)
class CleanupContext:
    project: str
    demo_id: str
    expires_at: str


def derive_cleanup_context(state: dict[str, Any]) -> CleanupContext:
    """Return the single validated cleanup context from a Terraform state document."""

    resources = state.get("resources")
    if not isinstance(resources, list):
        raise CleanupContextRejected("Terraform state has no resources list.")

    matches = [
        resource
        for resource in resources
        if resource.get("type") == CONTEXT_RESOURCE_TYPE and resource.get("name") == CONTEXT_RESOURCE_NAME
    ]
    if len(matches) != 1:
        raise CleanupContextRejected("Terraform state must contain exactly one cleanup context resource.")

    instances = matches[0].get("instances")
    if not isinstance(instances, list) or len(instances) != 1:
        raise CleanupContextRejected("Cleanup context resource must contain exactly one instance.")

    attributes = instances[0].get("attributes")
    context = attributes.get("input") if isinstance(attributes, dict) else None
    if isinstance(context, dict) and isinstance(context.get("value"), dict):
        context = context["value"]
    if not isinstance(context, dict):
        raise CleanupContextRejected("Cleanup context resource has no input object.")

    project = context.get("project")
    demo_id = context.get("demo_id")
    expires_at = context.get("expires_at")
    if project != PROJECT_NAME:
        raise CleanupContextRejected("Cleanup context has an unexpected project name.")
    if not isinstance(demo_id, str) or not DEMO_ID_PATTERN.fullmatch(demo_id):
        raise CleanupContextRejected("Cleanup context has an invalid demo identifier.")
    if not isinstance(expires_at, str) or not EXPIRES_AT_PATTERN.fullmatch(expires_at):
        raise CleanupContextRejected("Cleanup context has an invalid expiry timestamp.")

    return CleanupContext(project=project, demo_id=demo_id, expires_at=expires_at)
