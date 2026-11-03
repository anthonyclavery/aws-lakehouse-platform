import importlib.util
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock

PATH = Path(__file__).parents[1] / "jobs/silver_process_reference.py"
SPEC = importlib.util.spec_from_file_location("reference_write_guard", PATH)
JOB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(JOB)


class ReferenceWriteGuardTests(TestCase):
    def test_first_load_creates_table_without_merge(self):
        spark, incoming = Mock(), Mock()
        spark.catalog.tableExists.return_value = False
        self.assertTrue(JOB.apply_reference_updates(spark, "glue_catalog.demo.refs", incoming, ["site_id", "valid_from"])["created"])
        incoming.writeTo.assert_called_once_with("glue_catalog.demo.refs")
        incoming.writeTo.return_value.using.assert_called_once_with("iceberg")
        spark.sql.assert_not_called()

    def test_no_effective_rows_means_no_iceberg_write(self):
        spark, incoming = Mock(), Mock()
        effective = spark.sql.return_value.cache.return_value
        effective.count.return_value = 0
        result = JOB.apply_reference_updates(spark, "glue_catalog.demo.refs", incoming, ["site_id", "valid_from"])
        self.assertEqual(0, result["changed_reference_rows"])
        self.assertEqual(1, spark.sql.call_count)
        sql = spark.sql.call_args.args[0]
        self.assertIn("source.source_version > target.source_version", sql)
        self.assertIn("target.`site_id` IS NULL", sql)
        self.assertIn("target.`valid_from` = source.`valid_from`", sql)
        effective.createOrReplaceTempView.assert_not_called()
        effective.unpersist.assert_called_once()

    def test_new_keys_and_higher_versions_keep_guarded_merge(self):
        spark, incoming = Mock(), Mock()
        effective = spark.sql.return_value.cache.return_value
        effective.count.return_value = 3
        result = JOB.apply_reference_updates(spark, "glue_catalog.demo.refs", incoming, ["contract_id"])
        self.assertEqual(3, result["changed_reference_rows"])
        merge = spark.sql.call_args_list[1].args[0]
        self.assertIn("USING effective_reference", merge)
        self.assertIn("source.source_version > target.source_version", merge)
        self.assertIn("WHEN NOT MATCHED THEN INSERT *", merge)
        effective.unpersist.assert_called_once()

    def test_merge_failure_propagates_and_releases_cache(self):
        spark, incoming = Mock(), Mock()
        frame, effective = Mock(), Mock()
        frame.cache.return_value = effective
        effective.count.return_value = 1
        spark.sql.side_effect = [frame, RuntimeError("commit failed")]
        with self.assertRaises(RuntimeError): JOB.apply_reference_updates(spark, "glue_catalog.demo.refs", incoming, ["contract_id"])
        effective.unpersist.assert_called_once()
