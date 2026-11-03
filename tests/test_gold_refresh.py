"""Fail-closed entry points and checkpoint recovery, without Spark/AWS imports."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lakehouse_platform import gold_refresh as refresh
from lakehouse_platform.glue_runner import GlueInvocationRejected, build_gold_arguments


class GoldRefreshTests(unittest.TestCase):
    def args(self):
        p, a = build_gold_arguments("demo", "gold-delta")
        return {**p, **a}

    def test_all_arguments_and_derived_tables_are_validated_before_spark(self):
        args = self.args()
        self.assertEqual(["glue_catalog.demo.priced_intervals_refresh", "glue_catalog.demo.priced_intervals_stage"], refresh.validate(args))
        for batch in ("x", "UPPER", "x' OR 1=1", "a" * 65):
            with self.subTest(batch=batch), self.assertRaises(ValueError):
                refresh.validate({**args, "BATCH_ID": batch})
        for key, value in (("REFRESH_MODE", "unsafe"), ("INTERVAL_TABLE", args["PRICED_INTERVAL_TABLE"]),
                           ("INTERVAL_TABLE", args["PRICED_INTERVAL_TABLE"] + "_stage"),
                           ("DAILY_SITE_TABLE", "glue_catalog.demo.a;DROP TABLE b")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                refresh.validate({**args, key: value})

    def test_runner_propagates_matching_identity_and_mode_to_both_stages(self):
        for mode in ("bounded", "full"):
            price, aggregate = build_gold_arguments("demo", "gold-delta", mode)
            self.assertEqual(price["BATCH_ID"], aggregate["BATCH_ID"])
            self.assertEqual(mode, aggregate["REFRESH_MODE"])
        for batch, mode in (("bad_batch", "bounded"), ("valid-id", "unknown")):
            with self.assertRaises(GlueInvocationRejected):
                build_gold_arguments("demo", batch, mode)

    def test_pending_other_run_is_rejected(self):
        spark = Mock()
        spark.sql.return_value.count.return_value = 1
        with self.assertRaisesRegex(ValueError, "pending Gold"):
            refresh.check_pending(spark, "control", "gold-other")

    def test_retry_keeps_original_mode_and_does_not_recapture_sources(self):
        args = self.args()
        spark = Mock()
        with patch.object(refresh, "check_pending"), patch.object(refresh, "state", return_value={"mode": "full", "tables_json": refresh.table_identity(args)}):
            with self.assertRaisesRegex(ValueError, "original REFRESH_MODE"):
                refresh.start(spark, args, "control")
        spark.table.assert_not_called()

    def test_completed_run_returns_without_source_reads(self):
        args = self.args()
        spark = Mock()
        existing = {"mode": "bounded", "state": "DONE", "tables_json": refresh.table_identity(args)}
        with patch.object(refresh, "check_pending"), patch.object(refresh, "state", return_value=existing):
            self.assertEqual(existing, refresh.start(spark, args, "control"))
        spark.table.assert_not_called()

    def test_retry_refuses_changed_source_or_destination_mapping(self):
        original = self.args()
        existing = {"mode": "bounded", "state": "READY", "tables_json": refresh.table_identity(original)}
        for field in ("DAILY_SITE_TABLE", "INTERVAL_TABLE", "TARIFF_TABLE"):
            args = {**original, field: original[field] + "_changed"}
            with self.subTest(field=field), patch.object(refresh, "check_pending"), patch.object(refresh, "state", return_value=existing):
                with self.assertRaisesRegex(ValueError, "original source and destination"):
                    refresh.start(Mock(), args, "control")

    def test_failed_preparation_discards_only_confirmed_unpublished_attempt(self):
        modules = {"pyspark": SimpleNamespace(), "pyspark.sql": SimpleNamespace(functions=Mock()),
                   "gold_spark": SimpleNamespace(SEGMENT_COLUMNS=(), SEGMENT_SCHEMA="")}
        for persisted in ("PREPARING", "READY"):
            spark = Mock()
            with self.subTest(persisted=persisted), patch.dict("sys.modules", modules), \
                    patch.object(refresh, "controls", return_value=("control", "stage")), \
                    patch.object(refresh, "start", return_value={"state": "PREPARING"}), \
                    patch.object(refresh, "prepare", side_effect=RuntimeError("validation failed")), \
                    patch.object(refresh, "state", return_value={"state": persisted}):
                with self.assertRaisesRegex(RuntimeError, "validation failed"):
                    refresh.price(spark, self.args())
            self.assertEqual(2 if persisted == "PREPARING" else 0, spark.sql.call_count)
            if persisted == "PREPARING":
                self.assertIn("AND state = 'PREPARING'", spark.sql.call_args.args[0])

    def test_empty_snapshot_is_not_replaced_with_current_source(self):
        spark = Mock()
        refresh.at(spark, "source", "")
        spark.table.return_value.limit.assert_called_once_with(0)
        spark.read.format.assert_not_called()

    def test_expired_snapshot_error_propagates_without_falling_back_to_current(self):
        spark = Mock()
        spark.read.format.return_value.option.return_value.load.side_effect = RuntimeError("expired")
        with self.assertRaisesRegex(RuntimeError, "expired"):
            refresh.at(spark, "source", "123")
        spark.table.assert_not_called()

    def test_unchanged_target_skips_merge_and_releases_materialized_rows(self):
        spark = Mock()
        mutations = spark.sql.return_value.cache.return_value
        mutations.count.return_value = 0
        self.assertEqual(0, refresh.merge_rows(spark, "target", Mock(), ["id"], ["id", "value"]))
        self.assertEqual(1, spark.sql.call_count)
        mutations.unpersist.assert_called_once()

    def test_native_merge_error_propagates_and_releases_rows(self):
        spark = Mock()
        mutations = Mock()
        mutations.count.return_value = 1
        first = Mock()
        first.cache.return_value = mutations
        spark.sql.side_effect = [first, RuntimeError("commit failed")]
        with self.assertRaisesRegex(RuntimeError, "commit failed"):
            refresh.merge_rows(spark, "target", Mock(), ["id"], ["id", "value"])
        mutations.unpersist.assert_called_once()
