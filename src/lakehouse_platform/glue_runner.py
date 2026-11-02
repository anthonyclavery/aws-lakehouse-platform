"""Validated arguments for explicit AWS Glue job invocations."""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from .reference_processing import BUSINESS_KEYS


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


def build_gold_arguments(database: str) -> tuple[dict[str, str], dict[str, str]]:
    """Use separate source and destination tables in the demo Glue catalog."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", database):
        raise GlueInvocationRejected("database must be a lowercase Glue database identifier")
    names = {
        "INTERVAL_TABLE": "meter_consumption_interval", "SITE_METER_TABLE": "site_meter_history",
        "CUSTOMER_SITE_TABLE": "customer_site_history", "SITE_CONTRACT_TABLE": "site_contract_history",
        "TARIFF_TABLE": "tariff_grid_history", "PRICED_INTERVAL_TABLE": "priced_intervals",
        "DAILY_SITE_TABLE": "daily_site_energy", "DAILY_CUSTOMER_TABLE": "daily_customer_energy",
        "MONTHLY_CUSTOMER_TABLE": "monthly_customer_energy",
    }
    tables = {key: f"glue_catalog.{database}.{name}" for key, name in names.items()}
    return ({key: value for key, value in tables.items() if key not in {
        "DAILY_SITE_TABLE", "DAILY_CUSTOMER_TABLE", "MONTHLY_CUSTOMER_TABLE"}},
        {key: tables[key] for key in ("PRICED_INTERVAL_TABLE", "DAILY_SITE_TABLE", "DAILY_CUSTOMER_TABLE", "MONTHLY_CUSTOMER_TABLE")})


def run_gold_jobs(glue, pricing_job, aggregate_job, database, *, wait_seconds=3600, poll_seconds=15):
    """Run pricing then aggregation, failing closed on any unsuccessful state."""
    return run_glue_jobs(glue, zip((pricing_job, aggregate_job), build_gold_arguments(database)),
                         wait_seconds=wait_seconds, poll_seconds=poll_seconds)


def build_reference_arguments(database: str, batch_path: str, flows=None) -> list[dict[str, str]]:
    """Resolve one immutable batch into file-specific reference invocations."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", database):
        raise GlueInvocationRejected("database must be a lowercase Glue database identifier")
    uri = urlsplit(batch_path)
    if (uri.scheme != "s3" or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", uri.netloc)
            or not re.fullmatch(r"/landing/batch_id=[a-z0-9][a-z0-9-]{2,63}/?", uri.path)
            or uri.query or uri.fragment):
        raise GlueInvocationRejected("batch_path must be s3://BUCKET/landing/batch_id=BATCH_ID")
    selected = list(BUSINESS_KEYS) if flows is None else list(flows)
    if not selected or len(set(selected)) != len(selected) or any(flow not in BUSINESS_KEYS for flow in selected):
        raise GlueInvocationRejected("Select distinct supported reference flows")
    return [{"LANDING_PATH": f"{batch_path.rstrip('/')}/reference/{flow}.jsonl",
             "TARGET_TABLE": f"glue_catalog.{database}.{flow}_history",
             "BUSINESS_KEY_COLUMNS": ",".join(BUSINESS_KEYS[flow])} for flow in selected]


def run_reference_jobs(glue, job_name, database, batch_path, flows=None, *, wait_seconds=3600, poll_seconds=15):
    arguments = build_reference_arguments(database, batch_path, flows)
    return run_glue_jobs(glue, ((job_name, values) for values in arguments),
                         wait_seconds=wait_seconds, poll_seconds=poll_seconds)


def run_glue_jobs(glue, invocations, *, wait_seconds=3600, poll_seconds=15):
    """Wait for each invocation to succeed before starting the next one."""
    import time

    runs = []
    for job_name, arguments in invocations:
        run_id = glue.start_job_run(JobName=job_name, JobRunQueuingEnabled=True,
                                    Arguments={f"--{k}": v for k, v in arguments.items()})["JobRunId"]
        print(f"Started {job_name}: {run_id}", flush=True)
        deadline = time.monotonic() + wait_seconds
        while True:
            state = glue.get_job_run(JobName=job_name, RunId=run_id, PredecessorsIncluded=False)["JobRun"]["JobRunState"]
            if state == "SUCCEEDED":
                runs.append(run_id)
                break
            if state in {"FAILED", "STOPPED", "TIMEOUT", "ERROR", "EXPIRED"}:
                raise RuntimeError(f"{job_name} run {run_id} finished with {state}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Stopped waiting for {job_name} run {run_id}; inspect it before retrying")
            time.sleep(poll_seconds)
    return runs
