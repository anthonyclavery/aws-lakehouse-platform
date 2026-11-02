import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from lakehouse_platform.generator import GeneratorConfig, write_initial_batch
from lakehouse_platform.glue_runner import (
    GlueInvocationRejected, build_gold_arguments, build_reference_arguments, run_reference_jobs,
)
from lakehouse_platform.landing import plan_landing_upload
from lakehouse_platform.reference_processing import BUSINESS_KEYS

ROOT = Path(__file__).parents[1]
BATCH = "s3://aws-lakehouse-platform-synthetic/landing/batch_id=initial-demo"


class ReferenceRunnerTests(unittest.TestCase):
    def test_all_flows_use_isolated_files_and_canonical_business_keys(self):
        runs = build_reference_arguments("demo_db", BATCH + "/")
        gold, _ = build_gold_arguments("demo_db")
        gold_tables = {gold[key] for key in ("SITE_METER_TABLE", "CUSTOMER_SITE_TABLE", "SITE_CONTRACT_TABLE", "TARIFF_TABLE")}
        self.assertEqual(gold_tables, {run["TARGET_TABLE"] for run in runs})
        for flow, run in zip(BUSINESS_KEYS, runs):
            self.assertEqual(BATCH + f"/reference/{flow}.jsonl", run["LANDING_PATH"])
            self.assertEqual(",".join(BUSINESS_KEYS[flow]), run["BUSINESS_KEY_COLUMNS"])

    def test_arguments_match_actual_generator_manifest_upload_keys(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_initial_batch(GeneratorConfig(customers=2, sites=3, include_indexes=False, output_dir=root))
            uploaded = {f"s3://aws-lakehouse-platform-synthetic/{item.key}" for item in plan_landing_upload(root, "initial-demo")}
            self.assertTrue({r["LANDING_PATH"] for r in build_reference_arguments("demo_db", BATCH)} <= uploaded)

    def test_partial_batch_selects_only_requested_files(self):
        runs = build_reference_arguments("demo_db", BATCH, ["tariff_grid", "site_contract"])
        self.assertEqual(["tariff_rule_id", "contract_id"], [r["BUSINESS_KEY_COLUMNS"] for r in runs])

    def test_invalid_batch_paths_are_rejected(self):
        for value in ("s3://bucket/", "https://bucket/landing/batch_id=abc", "s3://bucket/landing/",
                      BATCH + "/reference", BATCH + "/../other", BATCH + "?query=1", BATCH + "#fragment",
                      BATCH.replace("initial-demo", "../escape"), BATCH.replace("initial-demo", "A"),
                      "s3://user@bucket/landing/batch_id=abc"):
            with self.subTest(value=value), self.assertRaises(GlueInvocationRejected):
                build_reference_arguments("demo_db", value)

    def test_invalid_database_or_flow_selection_is_rejected(self):
        for database in ("", "db.table", "db;DROP", "UPPER"):
            with self.subTest(database=database), self.assertRaises(GlueInvocationRejected):
                build_reference_arguments(database, BATCH)
        for flows in ([], ["unknown"], ["site_meter", "site_meter"]):
            with self.subTest(flows=flows), self.assertRaises(GlueInvocationRejected):
                build_reference_arguments("demo_db", BATCH, flows)

    @patch("time.sleep")
    def test_four_runs_are_sequential_and_wait_for_each_success(self, sleep):
        glue = Mock()
        glue.start_job_run.side_effect = [{"JobRunId": str(i)} for i in range(4)]
        glue.get_job_run.side_effect = [{"JobRun": {"JobRunState": state}} for state in
            ("RUNNING", "SUCCEEDED", "SUCCEEDED", "SUCCEEDED", "SUCCEEDED")]
        self.assertEqual(["0", "1", "2", "3"], run_reference_jobs(glue, "ref-job", "demo_db", BATCH))
        targets = [call.kwargs["Arguments"]["--TARGET_TABLE"] for call in glue.start_job_run.call_args_list]
        self.assertEqual([f"glue_catalog.demo_db.{flow}_history" for flow in BUSINESS_KEYS], targets)
        self.assertEqual(["0", "0", "1", "2", "3"], [call.kwargs["RunId"] for call in glue.get_job_run.call_args_list])
        sleep.assert_called_once()

    def test_failure_stops_remaining_flows(self):
        glue = Mock()
        glue.start_job_run.return_value = {"JobRunId": "failed"}
        glue.get_job_run.return_value = {"JobRun": {"JobRunState": "FAILED"}}
        with self.assertRaises(RuntimeError):
            run_reference_jobs(glue, "ref-job", "demo_db", BATCH)
        glue.start_job_run.assert_called_once()

    def test_dry_run_cli_validates_arguments_offline(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/run_silver_reference_jobs.py"),
            "--job-name", "reference-demo", "--database", "demo_db", "--batch-path", BATCH,
            "--flows", "tariff_grid", "--dry-run"], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        result = json.loads(result.stdout)
        self.assertEqual("reference-demo", result["job_name"])
        self.assertEqual(build_reference_arguments("demo_db", BATCH, ["tariff_grid"]), result["runs"])
