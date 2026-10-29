"""Validated arguments for explicit AWS Glue job invocations."""

from __future__ import annotations

from dataclasses import dataclass


class GlueInvocationRejected(ValueError):
    """Raised when a script-driven Glue invocation is outside the demo scope."""


@dataclass(frozen=True)
class SilverIndexInvocation:
    batch_id: str
    landing_path: str
    silver_index_table: str
    silver_interval_table: str
    quarantine_table: str


def build_silver_index_arguments(invocation: SilverIndexInvocation) -> dict[str, str]:
    if not invocation.batch_id or not invocation.batch_id.isascii():
        raise GlueInvocationRejected("batch_id must be a non-empty ASCII identifier.")
    if not invocation.landing_path.startswith("s3://") or "/landing/" not in invocation.landing_path:
        raise GlueInvocationRejected("landing_path must be an S3 landing location.")
    tables = {
        "SILVER_INDEX_TABLE": invocation.silver_index_table,
        "SILVER_INTERVAL_TABLE": invocation.silver_interval_table,
        "QUARANTINE_TABLE": invocation.quarantine_table,
    }
    if any(not table.startswith("glue_catalog.") for table in tables.values()):
        raise GlueInvocationRejected("Silver and quarantine tables must use the glue_catalog Iceberg catalog.")
    return {"BATCH_ID": invocation.batch_id, "LANDING_PATH": invocation.landing_path, **tables}
