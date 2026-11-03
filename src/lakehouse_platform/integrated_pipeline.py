"""Explicit manifest-backed ingestion -> reference/Silver -> Gold execution."""
import json
import re
from pathlib import Path

from .glue_runner import (SilverIndexInvocation, build_gold_arguments, build_reference_arguments,
                          build_silver_index_arguments, run_glue_jobs)
from .landing import plan_landing_upload, upload_landing_batch


def build_integrated_invocations(values, batch_id, *, load_references):
    location = values["landing_path"]
    match = re.fullmatch(r"s3://(aws-lakehouse-platform-([a-z0-9-]{3,31}))-[a-f0-9]{8}/landing/", location)
    if not match or values["glue_database_name"] != match[1].replace("-", "_"):
        raise ValueError("Outputs must identify one isolated project bucket/database")
    prefix, database = match[1], values["glue_database_name"]
    expected_jobs = {"price": prefix + "-gold-price", "aggregate": prefix + "-gold-aggregate"}
    if values["gold_job_names"] != expected_jobs or values["silver_reference_job_name"] != prefix + "-silver-process-reference":
        raise ValueError("Refuse jobs outside this demonstration's exact pipeline")
    silver = build_silver_index_arguments(SilverIndexInvocation(batch_id, location + f"batch_id={batch_id}/consumption_index.jsonl",
        f"glue_catalog.{database}.consumption_index", f"glue_catalog.{database}.meter_consumption_interval",
        f"glue_catalog.{database}.quarantine_records"))
    gold = build_gold_arguments(database, batch_id, "bounded")
    stages = []
    if load_references:
        for flow, arguments in zip(("site_meter", "customer_site", "site_contract", "tariff_grid"),
                                   build_reference_arguments(database, location + f"batch_id={batch_id}")):
            stages.append(("reference:" + flow, values["silver_reference_job_name"], arguments))
    stages.append(("silver:indexes", prefix + "-silver-process-indexes", silver))
    stages.extend(("gold:" + name, expected_jobs[name], args) for name, args in zip(("price", "aggregate"), gold))
    return stages


def run_integrated_batch(s3, glue, values, batch_dir, batch_id, *, load_references, journal: Path, poll_seconds=15):
    stages = build_integrated_invocations(values, batch_id, load_references=load_references)
    planned = plan_landing_upload(batch_dir, batch_id)
    required = {"consumption_index.jsonl"}
    if load_references:
        required.update(f"reference/{flow}.jsonl" for flow in ("site_meter", "customer_site", "site_contract", "tariff_grid"))
    supplied = {item.path.relative_to(batch_dir.resolve()).as_posix() for item in planned[:-1]}
    if not required <= supplied:
        raise ValueError("The manifest does not supply every planned pipeline input")
    uploaded = upload_landing_batch(s3, values["landing_path"].split("/")[2], batch_dir, batch_id)
    completed = []
    for label, job, arguments in stages:
        class RecordingGlue:
            def start_job_run(self, **kwargs):
                response = glue.start_job_run(**kwargs)
                with journal.open("a") as output:
                    output.write(json.dumps({"stage": label, "job": job, "run_id": response["JobRunId"], "arguments": kwargs["Arguments"]}) + "\n")
                return response

            def get_job_run(self, **kwargs):
                response = glue.get_job_run(**kwargs)
                run = response["JobRun"]
                state = run["JobRunState"]
                if state in {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT", "ERROR", "EXPIRED"}:
                    summary = {"stage": label, "state": state, "execution_seconds": run.get("ExecutionTime")}
                    with journal.open("a") as output:
                        output.write(json.dumps({**summary, "run_id": kwargs["RunId"], "error": run.get("ErrorMessage")}) + "\n")
                    if state == "SUCCEEDED":
                        completed.append(summary)
                return response

        run_glue_jobs(RecordingGlue(), [(job, arguments)], poll_seconds=poll_seconds)
    return {"passed": True, "uploaded_source_objects": len(uploaded.uploaded_keys), "reused_source_objects": len(uploaded.existing_keys),
            "stages": completed, "source_manifest_last": planned[-1].key.endswith("/manifest.json")}
