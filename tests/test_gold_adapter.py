"""Exercise the exact standalone Python modules uploaded to Glue, without AWS."""

from datetime import date, datetime
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

from lakehouse_platform.pricing import PricingRejected
from test_gold_boundaries import interval, references

MODULES = Path(__file__).parents[1] / "src" / "lakehouse_platform"


def load_adapter():
    # Match --extra-py-files: modules are importable by their filename on Glue.
    spec = importlib.util.spec_from_file_location("gold_spark", MODULES / "gold_spark.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"pricing": sys.modules["lakehouse_platform.pricing"]}):
        spec.loader.exec_module(module)
    return module


adapter = load_adapter()


class GoldAdapterTests(unittest.TestCase):
    def test_naive_spark_timestamp_normalization_and_decimal_output(self):
        source = interval()
        source["interval_start"] = datetime(2026, 1, 5)
        source["interval_end"] = datetime(2026, 1, 5, 1)
        segment, = adapter.price_partition([source], references())
        row = dict(zip(adapter.SEGMENT_COLUMNS, segment))
        self.assertEqual(date(2026, 1, 5), row["local_date"])
        self.assertEqual(Decimal("1.000000000000"), row["consumption_kwh"])
        self.assertEqual(Decimal("0.200000000000"), row["energy_cost_eur"])

    def test_reference_broadcast_is_bounded_before_reading_intervals(self):
        spark = Mock()
        spark.table.return_value.limit.return_value.collect.return_value = [None] * (adapter.REFERENCE_LIMIT + 1)
        with self.assertRaises(ValueError):
            adapter.rebuild_priced(spark, {"SITE_METER_TABLE": "source"})
        spark.createDataFrame.assert_not_called()

    def test_validation_error_does_not_replace_table_and_releases_cache(self):
        spark = Mock()
        spark.table.return_value.limit.return_value.collect.return_value = []
        priced = spark.createDataFrame.return_value.cache.return_value
        priced.count.side_effect = PricingRejected("Tariff gap")
        args = {k: k for k in ("SITE_METER_TABLE", "CUSTOMER_SITE_TABLE", "SITE_CONTRACT_TABLE", "TARIFF_TABLE", "INTERVAL_TABLE", "PRICED_INTERVAL_TABLE")}
        with self.assertRaises(PricingRejected):
            adapter.rebuild_priced(spark, args)
        priced.writeTo.assert_not_called()
        priced.unpersist.assert_called_once()
        spark.sparkContext.broadcast.return_value.unpersist.assert_called_once()

    def test_empty_snapshot_still_replaces_old_rows(self):
        spark = Mock()
        spark.table.return_value.limit.return_value.collect.return_value = []
        priced = spark.createDataFrame.return_value.cache.return_value
        priced.count.return_value = 0
        args = {k: k for k in ("SITE_METER_TABLE", "CUSTOMER_SITE_TABLE", "SITE_CONTRACT_TABLE", "TARIFF_TABLE", "INTERVAL_TABLE", "PRICED_INTERVAL_TABLE")}
        self.assertEqual(0, adapter.rebuild_priced(spark, args))
        priced.writeTo.assert_called_once_with("PRICED_INTERVAL_TABLE")
        priced.writeTo.return_value.using.return_value.partitionedBy.return_value.createOrReplace.assert_called_once()
