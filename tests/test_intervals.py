from datetime import UTC, datetime, timedelta
import unittest

from lakehouse_platform.intervals import ConsumptionIndex, InvalidIndexSequence, derive_consumption_interval


class ConsumptionIntervalTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 1, tzinfo=UTC)

    def test_first_index_and_baseline_do_not_create_consumption(self):
        first = ConsumptionIndex("meter", self.start, 100)
        baseline = ConsumptionIndex("meter", self.start, 100, reading_context="INSTALLATION_BASELINE")
        self.assertIsNone(derive_consumption_interval(None, first))
        self.assertIsNone(derive_consumption_interval(first, baseline))

    def test_normal_interval(self):
        interval = derive_consumption_interval(
            ConsumptionIndex("meter", self.start, 100),
            ConsumptionIndex("meter", self.start + timedelta(hours=1), 850),
            expected_cadence_seconds=3600,
        )
        self.assertEqual(750, interval.consumption_wh)
        self.assertEqual("VALID", interval.quality_status)
        self.assertEqual("DIRECT", interval.allocation_method)

    def test_declared_rollover(self):
        interval = derive_consumption_interval(
            ConsumptionIndex("meter", self.start, 999_998_900),
            ConsumptionIndex("meter", self.start + timedelta(hours=1), 250, rollover_count=1),
        )
        self.assertEqual(351, interval.consumption_wh)

    def test_unexplained_regression_is_rejected(self):
        with self.assertRaises(InvalidIndexSequence):
            derive_consumption_interval(
                ConsumptionIndex("meter", self.start, 500),
                ConsumptionIndex("meter", self.start + timedelta(hours=1), 200),
            )

    def test_irregular_replacement_interval_is_valid_with_warning(self):
        interval = derive_consumption_interval(
            ConsumptionIndex("meter", self.start, 100),
            ConsumptionIndex("meter", self.start + timedelta(minutes=35), 450),
            expected_cadence_seconds=3600,
        )
        self.assertEqual("VALID_WITH_WARNING", interval.quality_status)
        self.assertEqual("PRO_RATA_TIME", interval.allocation_method)


if __name__ == "__main__":
    unittest.main()
