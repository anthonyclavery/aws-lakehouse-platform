from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lakehouse_platform.partition_benchmark import (
    Profile, build_invocation, build_tables, estimated_scan_cost, measure, query_sql, summarize, validate_manifest,
)


def manifest(profile):
    return {"profile": asdict(profile), "rows": profile.rows, "multiset_equal": True,
            "source_snapshots": {layout: "100" for layout in profile.tables}, "layouts": {
        layout: {"partition_spec": [] if layout == "unpartitioned" else [{"source_column": "interval_end", "transform": "day"}],
                 "records": profile.rows, "data_files": 2,
                 "properties": {"write.format.default": "parquet", "write.parquet.compression-codec": "zstd",
                                "write.target-file-size-bytes": "134217728", "write.distribution-mode": "none"}}
        for layout in profile.tables}}


class PartitionBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.profile = Profile("test_run", 2, 3, "2024-02-28")
        self.athena = Mock()
        self.athena.get_work_group.return_value = {"WorkGroup": {"Configuration": {
            "EnforceWorkGroupConfiguration": True, "BytesScannedCutoffPerQuery": 1073741824,
            "EngineVersion": {"SelectedEngineVersion": "Athena engine version 3"},
            "ResultConfiguration": {"EncryptionConfiguration": {"EncryptionOption": "SSE_S3"}}}}}

    def test_envelope_rejects_unsafe_ids_dates_and_large_or_noninteger_profiles(self):
        for name in ("", "x", "x;DROP TABLE y", "UPPER", "../x", "a" * 26):
            with self.subTest(name=name), self.assertRaises(ValueError):
                Profile(name)
        for days, meters in ((1, 3), (1097, 1), (1096, 1000), (2, 0), (2, 1001), (True, 3), (2, 2.5)):
            with self.subTest(days=days, meters=meters), self.assertRaises(ValueError):
                Profile("test", days, meters)
        for start in ("2024-02-30", "20240228", "2024-02-28'", "9999-12-31"):
            with self.subTest(start=start), self.assertRaises((ValueError, OverflowError)):
                Profile("test", 2, 3, start)

    def test_integer_oracle_matches_independent_enumeration_on_leap_day(self):
        rows = []
        start = datetime(2024, 2, 28)
        for row_id in range(144):
            hour, meter = divmod(row_id, 3)
            at = start + timedelta(hours=hour)
            if at.date().isoformat() == "2024-02-29":
                rows.append((meter, at, 100 + (row_id * 37 + hour * 13) % 4901))
        self.assertEqual({"rows": str(len(rows)), "meters": str(len({r[0] for r in rows})),
                          "consumption_wh": str(sum(r[2] for r in rows)), "first_reading": "2024-02-29 00:00:00",
                          "last_reading": "2024-02-29 23:00:00"}, self.profile.expected())

    def test_query_is_half_open_and_only_allows_the_profile_tables(self):
        sql = query_sql("demo", self.profile.tables["daily"], self.profile)
        self.assertIn("interval_end >= TIMESTAMP '2024-02-29 00:00:00'", sql)
        self.assertIn("interval_end < TIMESTAMP '2024-03-01 00:00:00'", sql)
        for db, table, day in (("db;DROP TABLE x", self.profile.tables["daily"], None),
                               ("demo", "foreign_table", None), ("demo", self.profile.tables["daily"], "2024-03-01")):
            with self.assertRaises(ValueError):
                query_sql(db, table, self.profile, day)

    def test_cost_model_applies_floor_rounding_and_separates_measured_bytes(self):
        for scanned, expected in ((0, 10_000_000), (10_000_000, 10_000_000), (10_000_001, 11_000_000)):
            self.assertEqual(expected, estimated_scan_cost(scanned)["rounded_bytes"])
        self.assertEqual("0.00005", estimated_scan_cost(1)["estimated_usd"])
        for value, rate in ((-1, Decimal(5)), (1.5, Decimal(5)), (1, Decimal("NaN")), (1, Decimal(-1))):
            with self.assertRaises(ValueError):
                estimated_scan_cost(value, rate)

    def invocation(self, **changes):
        args = {"database": "aws_lakehouse_platform_demo", "job": "aws-lakehouse-platform-demo-gold-price",
                "script_uri": "s3://aws-lakehouse-platform-demo-123/jobs/benchmark_partition_pruning.py",
                "module_uri": "s3://aws-lakehouse-platform-demo-123/jobs/partition_benchmark.py", "profile": self.profile}
        args.update(changes)
        return build_invocation(**args)

    def test_invocation_scopes_script_override_and_manifest_to_project(self):
        result = self.invocation()
        self.assertEqual("144", str(self.profile.rows))
        self.assertTrue(result["MANIFEST_URI"].endswith("/landing/batch_id=benchmark-test_run/benchmark-manifest.json"))
        for override in ({"database": "foreign"}, {"job": "foreign"},
                         {"script_uri": "s3://other/jobs/benchmark_partition_pruning.py"},
                         {"module_uri": "s3://aws-lakehouse-platform-other/jobs/partition_benchmark.py"},
                         {"script_uri": "s3://aws-lakehouse-platform-demo-123/jobs/arbitrary.py"},
                         {"module_uri": "s3://aws-lakehouse-platform-demo-123/jobs/partition_benchmark.py?x=1"}):
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.invocation(**override)

    def test_build_never_overwrites_either_existing_table(self):
        spark = Mock()
        spark.catalog.tableExists.side_effect = [False, True]
        with patch.dict("sys.modules", {"pyspark": SimpleNamespace(), "pyspark.sql": SimpleNamespace(functions=Mock())}):
            with self.assertRaisesRegex(ValueError, "already exist"):
                build_tables(spark, "demo", self.profile)
        spark.range.assert_not_called()

    def test_manifest_rejects_incomparable_layout_or_different_dataset(self):
        for mutate in (lambda m: m.update(multiset_equal=False), lambda m: m.update(rows=1),
                       lambda m: m["layouts"]["daily"].update(partition_spec=[]),
                       lambda m: m["layouts"]["daily"]["properties"].update({"write.parquet.compression-codec": "gzip"})):
            value = manifest(self.profile)
            mutate(value)
            with self.assertRaises(ValueError):
                validate_manifest(value, self.profile)

    def run_measure(self, *, mutate_stats=None, wrong_result=False, changed_snapshot=False, changed_build=False, repeats=3):
        snapshot_calls = []
        sample_calls = []
        def query(client, database, workgroup, sql):
            if "$history" in sql:
                snapshot_calls.append(sql)
                return [{"id": "200" if changed_build or (changed_snapshot and len(snapshot_calls) > 2) else "100"}], {}
            layout = "daily" if 'daily"' in sql else "unpartitioned"
            sample_calls.append(layout)
            stats = {"DataScannedInBytes": 100 if layout == "daily" else 1000,
                     "EngineExecutionTimeInMillis": len(sample_calls) * 10, "QueryPlanningTimeInMillis": 5,
                     "QueryQueueTimeInMillis": 0, "TotalExecutionTimeInMillis": len(sample_calls) * 10 + 1,
                     "ResultReuseInformation": {"ReusedPreviousResult": False}}
            if mutate_stats:
                mutate_stats(stats)
            return ([{"rows": "wrong"}] if wrong_result else [self.profile.expected()]), stats
        with patch("lakehouse_platform.qualification.query_rows", side_effect=query):
            result = measure(self.athena, "demo", "workgroup", self.profile, manifest(self.profile), repeats=repeats)
        return result, sample_calls

    def test_alternates_layouts_validates_oracle_and_reports_individual_samples_and_medians(self):
        result, order = self.run_measure()
        self.assertEqual(["unpartitioned", "daily", "daily", "unpartitioned", "unpartitioned", "daily"], order)
        self.assertEqual(3, len(result["samples"]["daily"]))
        self.assertEqual(100, result["summaries"]["daily"]["median_bytes_scanned"])
        self.assertEqual("0.9", result["median_scan_reduction_fraction"])
        self.assertTrue(result["snapshots_unchanged"])
        self.assertTrue(result["results_equal_oracle"])
        self.assertNotIn("source_snapshots", result["build"])

    def test_rejects_reused_missing_negative_statistics_or_incorrect_results(self):
        for mutate in (lambda s: s["ResultReuseInformation"].update(ReusedPreviousResult=True),
                       lambda s: s.pop("ResultReuseInformation"), lambda s: s.pop("DataScannedInBytes"),
                       lambda s: s.update(DataScannedInBytes=-1)):
            with self.assertRaises(ValueError):
                self.run_measure(mutate_stats=mutate)
        with self.assertRaisesRegex(ValueError, "oracle"):
            self.run_measure(wrong_result=True)

    def test_rejects_changed_snapshots_invalid_workgroup_and_too_few_repetitions(self):
        with self.assertRaisesRegex(ValueError, "snapshots changed"):
            self.run_measure(changed_snapshot=True)
        with self.assertRaisesRegex(ValueError, "changed since"):
            self.run_measure(changed_build=True)
        with self.assertRaises(ValueError):
            self.run_measure(repeats=2)
        self.athena.get_work_group.return_value["WorkGroup"]["Configuration"]["EnforceWorkGroupConfiguration"] = False
        with self.assertRaisesRegex(ValueError, "enforced engine"):
            self.run_measure()

    def test_median_resists_outlier(self):
        rows = [{k: n for k in ("bytes_scanned", "engine_ms", "planning_ms", "queue_ms", "total_ms")} for n in (3, 999, 1)]
        self.assertEqual(3, summarize(rows)["median_engine_ms"])
