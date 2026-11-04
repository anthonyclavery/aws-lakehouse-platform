from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from lakehouse_platform.analyst_queries import QUERIES, render_query, validate_analyst_session
from lakehouse_platform.demo import PHASES, load_state, locked, prepare, read, render_report, run_scenarios, write
from lakehouse_platform.demo_aws import AWSJourney, expect_absent, source_digest, validate_deployment_plan
from lakehouse_platform.qualification import expected_products
from lakehouse_platform.replay_validation import REPLAY_TABLES, SILVER_TABLES
from test_integrated_pipeline import VALUES

REPO = Path(__file__).parents[1]


class ServiceError(Exception):
    def __init__(self, response, operation):
        self.response = response


AWS_ERRORS = {"botocore": SimpleNamespace(), "botocore.exceptions": SimpleNamespace(ClientError=ServiceError)}


class DemoTests(TestCase):
    def test_complete_offline_cli_and_regenerated_report(self):
        with TemporaryDirectory() as folder:
            workspace = Path(folder) / "demo"
            base = [sys.executable, str(REPO / "scripts/run_demo.py"), "--workspace", str(workspace)]
            for command in (["prepare", "--run-id", "example-run"], ["local"], ["status"], ["report", "--mode", "local"]):
                result = subprocess.run([*base, *command], capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
            result = read(workspace / "local-report.json")
            self.assertEqual("completed", result["status"])
            self.assertEqual(5, len(result["phases"]))
            for phase in result["phases"].values():
                self.assertEqual(30000, phase["raw_energy_wh"])
                self.assertEqual("local oracle", phase["origin"])
            self.assertEqual(Decimal("4.871428571428"), Decimal(result["phases"]["correction"]["gold_eur"]))
            self.assertEqual(result["phases"]["correction"]["products"], result["phases"]["older-replay"]["products"])
            text = (workspace / "local-report.md").read_text()
            self.assertIn("does not measure AWS", text)
            self.assertIn("| initial | 9 | 6 | 30000 |", text)
            self.assertIn("| correction | 2.000000 | 1.400000 | 1.471429 |", text)
            self.assertLess(text.index("| initial |"), text.index("| late |"))
            self.assertEqual(3, len(list((workspace / "queries").glob("*.sql"))))
            self.assertNotIn("arn:aws:", text)
            self.assertNotIn(str(workspace), text)

    def test_source_mutation_duplicate_prepare_and_lock_fail_closed(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "demo"
            prepare(root, "example-run")
            with self.assertRaises(FileExistsError): prepare(root, "example-run")
            with locked(root):
                with self.assertRaises(FileExistsError):
                    with locked(root): pass
            self.assertFalse((root / ".operation.lock").exists())
            (root / "batches/late/extra.jsonl").write_text("{}")
            with self.assertRaises(ValueError): load_state(root)

    def test_partial_aws_failure_preserves_completed_evidence_and_never_resubmits(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "demo"
            state = prepare(root, "example-run")
            state["aws_status"] = "deployed"
            write(root / "state.json", state)
            phases = []
            def execute(phase, source, history, references):
                phases.append(phase)
                if phase == "late": raise TimeoutError("server run remains active")
                return expected_products(history), {"managed execution": True}
            with self.assertRaises(TimeoutError): run_scenarios(root, execute)
            self.assertEqual(["initial", "late"], phases)
            self.assertEqual("failed", load_state(root)["aws_status"])
            report = read(root / "aws-report.json")
            self.assertEqual("failed", report["status"])
            self.assertEqual({"initial"}, set(report["phases"]))
            self.assertIn("incomplete", render_report(report))
            with self.assertRaises(ValueError): run_scenarios(root, execute)
            self.assertEqual(["initial", "late"], phases)

    def test_deployment_guard_rejects_persistent_mutation_replacement_and_deletion(self):
        row = {"mode": "managed", "address": "aws_glue_job.silver_process_reference", "change": {"actions": ["create"]}}
        self.assertEqual(1, len(validate_deployment_plan({"resource_changes": [row]})))
        for address, actions in (("aws_iam_role.glue_silver", ["update"]), (row["address"], ["delete", "create"]),
                                 (row["address"], ["delete"])):
            bad = {**row, "address": address, "change": {"actions": actions}}
            with self.assertRaises(ValueError): validate_deployment_plan({"resource_changes": [bad]})

    def test_changed_saved_plan_and_changed_runtime_are_rejected_before_apply(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "demo"
            state = prepare(root, "example-run")
            aws = root / "aws"
            aws.mkdir()
            (aws / "deploy.tfplan").write_bytes(b"changed plan")
            state.update(aws_status="planned", source_sha256=source_digest(REPO), plan_sha256="0" * 64)
            write(root / "state.json", state)
            journey = AWSJourney.__new__(AWSJourney)
            journey.workspace, journey.repo, journey.aws = root, REPO, aws
            journey.command = Mock()
            with self.assertRaises(ValueError): journey.deploy()
            journey.command.assert_not_called()
            import hashlib
            state.update(source_sha256="0" * 64, plan_sha256=hashlib.sha256((aws / "deploy.tfplan").read_bytes()).hexdigest())
            write(root / "state.json", state)
            with self.assertRaises(ValueError): journey.deploy()
            journey.command.assert_not_called()

    def test_aws_adapter_sequences_normal_launchers_governance_audits_and_replays(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "demo"
            state = prepare(root, "example-run")
            state.update(aws_status="deployed", source_sha256=source_digest(REPO))
            write(root / "state.json", state)
            journey = AWSJourney.__new__(AWSJourney)
            journey.workspace, journey.aws, journey.repo = root, root / "aws", REPO
            journey.aws.mkdir()
            journey.project_profile, journey.governance_profile, journey.region = "project", "governance", "eu-west-3"
            journey.operator, journey.admin = Mock(), Mock()
            journey.admin.client.return_value.get_data_lake_settings.return_value = {"DataLakeSettings": {}}
            values = {**VALUES, "analyst_access": {}, "ingestion_access": {}}
            journey.values = Mock(return_value=values)
            journey.analyst = Mock(return_value=(Mock(), "analyst-workgroup"))
            calls, current = [], {}
            def script(name, arguments, log):
                arguments = list(map(str, arguments))
                calls.append((name, arguments))
                def argument(key): return arguments[arguments.index(key) + 1]
                if name == "run_integrated_batch.py":
                    current["phase"] = Path(argument("--artifact-dir")).name
                    current["history"] = next(row[2] for row in PHASES if row[0] == current["phase"])
                    Path(argument("--artifact-dir")).mkdir()
                elif name == "enable_analyst_access.py":
                    directory = Path(argument("--artifact-dir"))
                    directory.mkdir()
                    write(directory / "lf-bootstrap-journal.json", {"before": {}, "restored": True})
                    write(directory / "outputs.json", {})
                elif name == "qualify_integrated_silver.py":
                    write(Path(argument("--report")), {"passed": True, "tables": {
                        table: {"actual_rows": 1, "actual_sha256": "a" * 64} for table in SILVER_TABLES}})
                elif name == "qualify_analyst_access.py":
                    expected = read(argument("--expected"))
                    write(Path(argument("--report")), {"passed": True, "results_reused": False,
                        "temporary_credentials_used": True, "gold_select_restored": True, "gold_products": {
                            table: {"actual_rows": len(rows), "actual_sha256": "b" * 64} for table, rows in expected.items()}})
            journey.script = script
            def query(athena, database, workgroup, sql):
                products = expected_products([root / "batches" / name for name in current["history"]])
                table = next(table for table in products if '"' + table + '"' in sql)
                return products[table], {"ResultReuseInformation": {"ReusedPreviousResult": False}}
            metadata = {table: "c" * 64 for table in REPLAY_TABLES}
            with patch("lakehouse_platform.demo_aws.capture_replay_metadata", return_value=metadata), patch("lakehouse_platform.demo_aws.query_rows", side_effect=query):
                report = journey.run()
            self.assertEqual("completed", report["status"])
            pipeline = [args for name, args in calls if name == "run_integrated_batch.py"]
            self.assertEqual(5, len(pipeline))
            self.assertEqual(23, sum(7 if "--load-references" in args else 3 for args in pipeline))
            self.assertEqual(["example-run-initial", "example-run-late", "example-run-correction", "example-run-correction", "example-run-initial"],
                [args[args.index("--batch-id") + 1] for args in pipeline])
            self.assertEqual(5, len({args[args.index("--gold-batch-id") + 1] for args in pipeline}))
            self.assertEqual(1, sum(name == "enable_analyst_access.py" for name, _ in calls))
            self.assertTrue(read(journey.aws / "older-replay-stability.json")["passed"])
            self.assertEqual("AWS analyst", report["phases"]["correction"]["origin"])

    @patch.dict("sys.modules", AWS_ERRORS)
    def test_cleanup_refuses_wrong_context_and_active_jobs(self):
        with TemporaryDirectory() as folder:
            journey, context = self.cleanup_fixture(Path(folder))
            bad = deepcopy(context)
            bad["resources"][0]["instances"][0]["attributes"]["input"]["demo_id"] = "other-demo"
            journey.command = Mock(return_value=json.dumps(bad))
            with self.assertRaises(ValueError): journey.cleanup()
            journey.admin.client.assert_not_called()
            journey.command.return_value = json.dumps(context)
            write(journey.aws / "outputs.json", {})
            journey.values = Mock(return_value=VALUES)
            journey.admin.client.return_value.get_job_runs.return_value = {"JobRuns": [{"JobRunState": "RUNNING"}]}
            with self.assertRaises(ValueError): journey.cleanup()
            journey.admin.client.return_value.start_build.assert_not_called()

    def cleanup_fixture(self, parent):
        root = parent / "demo"
        state = prepare(root, "example-run")
        state["aws_status"] = "deploying"
        write(root / "state.json", state)
        journey = AWSJourney.__new__(AWSJourney)
        journey.workspace, journey.aws = root, root / "aws"
        journey.aws.mkdir()
        variables = {"demo_id": "example", "expires_at": "2026-11-04T23:00:00Z"}
        write(journey.aws / "variables.tfvars.json", variables)
        context = {"resources": [{"type": "terraform_data", "name": "cleanup_context", "instances": [{"attributes": {
            "input": {"project": "aws-lakehouse-platform", **variables}}}]}]}
        journey.admin = Mock()
        return journey, context

    def test_cleanup_uncertain_submission_is_not_reissued_and_known_build_is_polled(self):
        with TemporaryDirectory() as folder:
            journey, context = self.cleanup_fixture(Path(folder))
            journey.command = Mock(side_effect=[json.dumps(context), "revision", "", "origin/branch"])
            build = journey.admin.client.return_value
            build.start_build.side_effect = ConnectionError("response lost")
            with self.assertRaises(ConnectionError): journey.cleanup()
            self.assertEqual("cleaning", load_state(journey.workspace)["aws_status"])
            journey.command = Mock(return_value=json.dumps(context))
            with self.assertRaises(RuntimeError): journey.cleanup()
            self.assertEqual(1, build.start_build.call_count)
            state = load_state(journey.workspace)
            state["cleanup_build_id"] = "known-build"
            write(journey.workspace / "state.json", state)
            build.batch_get_builds.return_value = {"builds": [{"buildStatus": "SUCCEEDED"}]}
            journey.cleanup()
            self.assertEqual(1, build.start_build.call_count)
            self.assertEqual("cleaned", load_state(journey.workspace)["aws_status"])

    @patch.dict("sys.modules", AWS_ERRORS)
    def test_access_denied_is_not_accepted_as_resource_absence(self):
        from botocore.exceptions import ClientError
        missing = Mock(side_effect=ClientError({"Error": {"Code": "404"}}, "HeadBucket"))
        expect_absent(missing, {"404"})
        denied = Mock(side_effect=ClientError({"Error": {"Code": "403"}}, "HeadBucket"))
        with self.assertRaises(ClientError): expect_absent(denied, {"404"})
        with self.assertRaises(ValueError): expect_absent(Mock(), {"404"})

    def test_command_captures_streams_privately_and_fails_on_nonzero_exit(self):
        with TemporaryDirectory() as folder:
            journey = AWSJourney.__new__(AWSJourney)
            journey.workspace, journey.repo, journey.environment = Path(folder), REPO, os.environ.copy()
            text = journey.command([sys.executable, "-c", "import sys; print('result'); print('detail', file=sys.stderr)"], "command.log")
            self.assertEqual("result\n", text)
            self.assertEqual("detail\n", (Path(folder) / "command.log.stderr.log").read_text())
            with self.assertRaises(RuntimeError):
                journey.command([sys.executable, "-c", "raise SystemExit(2)"], "failed.log")


class AnalystQueryTests(TestCase):
    def test_query_reads_as_analyst_without_governance_or_backend_access(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "demo"
            state = prepare(root, "example-run")
            state.update(aws_status="completed", profiles={"project_profile": "project", "governance_profile": "admin", "region": "eu-west-3"})
            write(root / "state.json", state)
            operator = Mock()
            operator.client.return_value.get_caller_identity.return_value = {
                "Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/aws-lakehouse-platform-demo-terraform-operator/test"}
            session = Mock(return_value=operator)
            with patch.dict("sys.modules", {"boto3": SimpleNamespace(Session=session)}):
                journey = AWSJourney(root, Path(folder) / "no-terraform", "project", None, "eu-west-3", read_only=True)
            self.assertIsNone(journey.admin)
            session.assert_called_once_with(profile_name="project", region_name="eu-west-3")
            journey.values = Mock(return_value=VALUES)
            athena = Mock()
            journey.analyst = Mock(return_value=(athena, "analyst-workgroup"))
            statistics = {"ResultReuseInformation": {"ReusedPreviousResult": False}, "DataScannedInBytes": 42}
            with patch("lakehouse_platform.demo_aws.query_rows", return_value=([], statistics)) as query:
                result = journey.query("daily-site", "2026-01-09", "2026-01-11")
            self.assertEqual([], result["rows"])
            self.assertEqual(42, result["bytes_scanned"])
            self.assertEqual("analyst-workgroup", query.call_args.args[2])
            self.assertIn("local_date < DATE '2026-01-11'", query.call_args.args[3])
            with patch("lakehouse_platform.demo_aws.query_rows", return_value=([], {"ResultReuseInformation": {"ReusedPreviousResult": True}})):
                with self.assertRaises(ValueError): journey.query("daily-site", "2026-01-09", "2026-01-11")

    def test_gold_only_pack_half_open_dates_months_and_injection_refusal(self):
        for name, table in QUERIES.items():
            sql = render_query(name, "aws_lakehouse_platform_example", "2026-01-01", "2026-02-01")
            self.assertIn('"' + table + '"', sql)
            self.assertNotIn("SELECT *", sql)
            self.assertNotIn("warehouse", sql)
        sql = render_query("daily-site", "aws_lakehouse_platform_example", "2026-01-09", "2026-01-11")
        self.assertIn("local_date >= DATE '2026-01-09' AND local_date < DATE '2026-01-11'", sql)
        for arguments in (("daily-site", 'demo"; DROP TABLE x', "2026-01-01", "2026-02-01"),
                          ("daily-site", "aws_lakehouse_platform_example", "2026-02-01", "2026-01-01"),
                          ("monthly-customer", "aws_lakehouse_platform_example", "2026-01-09", "2026-02-01")):
            with self.assertRaises(ValueError): render_query(*arguments)
        with self.assertRaises(ValueError):
            render_query("daily-site", "aws_lakehouse_platform_example", "2026-01-01", "2026-02-01", "x' OR 1=1 --")

    def test_actual_analyst_identity_and_enforced_workgroup_are_required(self):
        session = Mock()
        account, database, bucket = "123456789012", "aws_lakehouse_platform_example", "aws-lakehouse-platform-example-aabbccdd"
        values = {"glue_database_name": database, "landing_path": f"s3://{bucket}/landing/", "analyst_access": {
            "role_arn": f"arn:aws:iam::{account}:role/aws-lakehouse-platform-example-analyst", "workgroup": "aws-lakehouse-platform-example-analyst"}}
        sts, athena = Mock(), Mock()
        session.client.side_effect = lambda name: {"sts": sts, "athena": athena}[name]
        sts.get_caller_identity.return_value = {"Arn": f"arn:aws:sts::{account}:assumed-role/aws-lakehouse-platform-example-analyst/session"}
        work = {"EnforceWorkGroupConfiguration": True, "BytesScannedCutoffPerQuery": 10000000,
                "ResultConfiguration": {"OutputLocation": f"s3://{bucket}/athena-results/analyst/", "EncryptionConfiguration": {"EncryptionOption": "SSE_S3"}},
                "EngineVersion": {"SelectedEngineVersion": "Athena engine version 3"}}
        athena.get_work_group.return_value = {"WorkGroup": {"Configuration": work}}
        self.assertEqual("aws-lakehouse-platform-example-analyst", validate_analyst_session(session, values, account))
        for key, value in (("EnforceWorkGroupConfiguration", False), ("BytesScannedCutoffPerQuery", 0)):
            bad = deepcopy(work)
            bad[key] = value
            athena.get_work_group.return_value = {"WorkGroup": {"Configuration": bad}}
            with self.assertRaises(ValueError): validate_analyst_session(session, values, account)
        sts.get_caller_identity.return_value = {"Arn": f"arn:aws:sts::{account}:assumed-role/operator/session"}
        with self.assertRaises(ValueError): validate_analyst_session(session, values, account)
