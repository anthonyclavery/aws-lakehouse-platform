from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lakehouse_platform import compaction_benchmark as c


def manifest(profile, phase="build"):
    result = {"profile": asdict(profile), "phase": phase, "rows": profile.rows, "multiset_equal": True,
              "source_snapshot": "100" if phase == "build" else "200",
              "layout": {"records": profile.rows, "data_files": profile.history_days * (profile.micro_batches if phase == "build" else 1),
                         "properties": dict(c.WRITER_PROPERTIES),
                         "partition_spec": [{"source_column": "interval_end", "transform": "day"}]}}
    if phase == "compact":
        result.update(before_snapshot="100", rewrite={"strategy": "binpack", "options": dict(c.OPTIONS), "recovered_existing_commit": True})
    return result


class CompactionBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.profile = c.CompactionProfile("compact_test", 4, 20, "2024-01-01", 4)
        self.before = manifest(self.profile)
        self.after = manifest(self.profile, "compact")

    def test_profile_envelope_and_default_volume(self):
        self.assertEqual(144000, c.CompactionProfile("default").rows)
        for days, meters, batches in ((91, 200, 4), (90, 500, 4), (2, 501, 4), (4, 20, 1), (4, 20, 21), (4, 20, True)):
            with self.subTest(days=days, meters=meters, batches=batches), self.assertRaises(ValueError):
                c.CompactionProfile("invalid", days, meters, "2024-01-01", batches)

    def test_exact_scoped_invocations_and_module_dependencies(self):
        script = "s3://aws-lakehouse-platform-demo-123/jobs/benchmark_compaction.py"
        module = "s3://aws-lakehouse-platform-demo-123/jobs/compaction_benchmark.py"
        args = c.invocation("aws_lakehouse_platform_demo", "aws-lakehouse-platform-demo-gold-price", script, module, self.profile, "compact")
        self.assertEqual("compact", args["PHASE"])
        self.assertIn("/jobs/partition_benchmark.py", args["extra-py-files"])
        self.assertTrue(args["MANIFEST_URI"].endswith("/landing/batch_id=compaction-compact_test/compact-manifest.json"))
        for bad_script, phase in ((script.replace("benchmark_compaction.py", "benchmark_partition_pruning.py"), "compact"), (script, "unknown")):
            with self.assertRaises(ValueError):
                c.invocation("aws_lakehouse_platform_demo", "aws-lakehouse-platform-demo-gold-price", bad_script, module, self.profile, phase)

    def test_rewrite_is_one_scoped_binpack_commit_without_expiration(self):
        sql = c.rewrite_statement("demo", self.profile)
        self.assertIn("table => 'demo.benchmark_compact_test_compaction'", sql)
        self.assertIn("'partial-progress.enabled', 'false'", sql)
        self.assertNotIn("expire", sql)
        with self.assertRaises(ValueError):
            c.rewrite_statement("db;DROP TABLE x", self.profile)

    def test_only_unchanged_baseline_or_direct_replace_child_can_recover(self):
        self.assertEqual("rewrite", c.recovery_action("100", "99", "append", "100"))
        self.assertEqual("verify_existing_commit", c.recovery_action("200", "100", "replace", "100"))
        for parent, operation in (("99", "replace"), ("100", "append"), ("100", "overwrite")):
            with self.assertRaises(ValueError):
                c.recovery_action("200", parent, operation, "100")

    def spark(self, current="200", parent="100", operation="replace"):
        spark = Mock()
        snapshot = spark._jvm.org.apache.iceberg.spark.Spark3Util.loadIcebergTable.return_value.currentSnapshot.return_value
        snapshot.snapshotId.return_value = int(current)
        snapshot.parentId.return_value = int(parent)
        snapshot.operation.return_value = operation
        old = spark.read.format.return_value.option.return_value.load.return_value
        new = spark.table.return_value
        old.schema = new.schema = object()
        old.exceptAll.return_value.limit.return_value.count.return_value = 0
        new.exceptAll.return_value.limit.return_value.count.return_value = 0
        return spark

    def test_retry_after_compaction_commit_checks_contents_without_rewriting(self):
        spark = self.spark()
        with patch.object(c, "table_details", return_value=(self.after["layout"], "200")):
            result = c.compact(spark, "demo", self.profile, self.before)
        self.assertTrue(result["rewrite"]["recovered_existing_commit"])
        spark.sql.assert_not_called()
        spark.read.format.return_value.option.assert_called_once_with("snapshot-id", "100")

    def test_injected_post_commit_interruption_does_not_claim_a_validated_manifest(self):
        spark = self.spark("100", "99", "append")
        with patch.object(c, "table_details") as details:
            with self.assertRaisesRegex(RuntimeError, "after the compaction commit"):
                c.compact(spark, "demo", self.profile, self.before, fail_after_rewrite=True)
        spark.sql.assert_called_once()
        details.assert_not_called()

    def test_changed_full_content_schema_or_no_file_reduction_fails_closed(self):
        spark = self.spark()
        spark.table.return_value.exceptAll.return_value.limit.return_value.count.return_value = 1
        with self.assertRaisesRegex(ValueError, "multiset"):
            c.compact(spark, "demo", self.profile, self.before)
        spark = self.spark()
        with patch.object(c, "table_details", return_value=(self.before["layout"], "200")):
            with self.assertRaisesRegex(ValueError, "did not reduce"):
                c.compact(spark, "demo", self.profile, self.before)

    def test_existing_fragmented_table_is_never_overwritten(self):
        spark = Mock()
        spark.catalog.tableExists.return_value = True
        with patch.dict("sys.modules", {"pyspark": SimpleNamespace(), "pyspark.sql": SimpleNamespace(functions=Mock())}):
            with self.assertRaisesRegex(ValueError, "exists"):
                c.build_fragmented(spark, "demo", self.profile)
        spark.range.assert_not_called()

    def measure(self, phase="build", *, reused=False, wrong=False, snapshots=None):
        athena = Mock()
        athena.get_work_group.return_value = {"WorkGroup": {"Configuration": {
            "EnforceWorkGroupConfiguration": True, "BytesScannedCutoffPerQuery": 1073741824,
            "EngineVersion": {"SelectedEngineVersion": "Athena engine version 3"},
            "ResultConfiguration": {"EncryptionConfiguration": {"EncryptionOption": "SSE_S3"}}}}}
        stats = {"DataScannedInBytes": 123, "EngineExecutionTimeInMillis": 900, "QueryPlanningTimeInMillis": 100,
                 "QueryQueueTimeInMillis": 10, "TotalExecutionTimeInMillis": 1000,
                 "ResultReuseInformation": {"ReusedPreviousResult": reused}}
        data = [{"rows": "bad"}] if wrong else [self.profile.expected()]
        value = self.before if phase == "build" else self.after
        with patch.object(c, "current_snapshot", side_effect=snapshots or [value["source_snapshot"]] * 2), \
                patch("lakehouse_platform.qualification.query_rows", return_value=(data, stats)):
            return c.measure_phase(athena, "demo", "group", self.profile, value)

    def test_before_after_oracle_measurements_and_snapshot_privacy(self):
        baseline, measured = self.measure(), self.measure("compact")
        result = c.report(self.profile, self.before, self.after, baseline, measured)
        self.assertEqual(3, len(result["before"]["measurements"]["samples"]))
        self.assertNotIn("source_snapshot", result["before"]["manifest"])
        self.assertNotIn("before_snapshot", result["after"]["manifest"])
        self.assertNotIn("source_snapshot", result["after"]["measurements"])
        self.assertTrue(result["full_multiset_preserved"])

    def test_wrong_result_reuse_stale_manifest_and_changed_snapshot_are_rejected(self):
        for options in ({"reused": True}, {"wrong": True}, {"snapshots": ["999"]}, {"snapshots": ["100", "999"]}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.measure(**options)

    def test_resume_checkpoint_refuses_wrong_profile_date_snapshot_or_truncated_metrics(self):
        original = self.measure()
        for key, value in (("repetitions", 4), ("source_snapshot", "999"), ("query_date", "2024-01-01"),
                           ("samples", []), ("summary", {})):
            data = deepcopy(original)
            data[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                c.validate_checkpoint(data, self.profile, self.before, 3, None)

    def test_report_refuses_cross_baseline_manifests_or_workgroup_changes(self):
        baseline, measured = self.measure(), self.measure("compact")
        after = deepcopy(self.after)
        after["before_snapshot"] = "99"
        with self.assertRaises(ValueError):
            c.report(self.profile, self.before, after, baseline, measured)
        measured["workgroup"]["scan_cap_bytes"] = 10000000
        with self.assertRaises(ValueError):
            c.report(self.profile, self.before, self.after, baseline, measured)
