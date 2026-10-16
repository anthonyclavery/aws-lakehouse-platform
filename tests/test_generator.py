from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lakehouse_platform.generator import GeneratorConfig, build_reference_records, iter_consumption_indexes, write_initial_batch
from lakehouse_platform.incremental import generate_incremental_batch
from lakehouse_platform.late_events import generate_exception_event
from lakehouse_platform.processing import process_indexes


class GeneratorTests(unittest.TestCase):
    def test_default_distribution_and_relationships(self):
        records = build_reference_records(GeneratorConfig(include_indexes=False))
        contracts = Counter(item["contract_type"] for item in records["site_contract"])
        owners = Counter(item["customer_id"] for item in records["customer_site"])
        self.assertEqual({"BASE": 100, "WEEKEND": 80, "PEAK_OFF_PEAK": 20}, contracts)
        self.assertEqual(200, len(records["site_meter"]))
        self.assertEqual(100, len(owners))
        self.assertEqual(20, sum(count > 1 for count in owners.values()))

    def test_small_batch_is_deterministic_and_writes_manifest(self):
        with TemporaryDirectory() as directory:
            common = dict(
                customers=4, sites=8, seed=7, include_indexes=False,
                as_of=datetime(2026, 1, 1, tzinfo=UTC),
            )
            manifest = write_initial_batch(GeneratorConfig(output_dir=Path(directory) / "first", **common))
            repeated_manifest = write_initial_batch(GeneratorConfig(output_dir=Path(directory) / "second", **common))
            self.assertEqual(8, manifest["record_counts"]["site_contract"])
            self.assertTrue((Path(directory) / "first" / "manifest.json").exists())
            self.assertEqual(4, len(manifest["files"]))
            self.assertEqual(manifest, repeated_manifest)

    def test_rollover_is_attached_to_the_lower_post_rollover_index(self):
        config = GeneratorConfig(
            customers=1, sites=1, seed=7, include_indexes=True,
            as_of=datetime(2026, 1, 3, tzinfo=UTC),
        )
        references = build_reference_records(config)
        references["site_contract"][0]["valid_from"] = "2023-01-03T00:00:00Z"
        indexes = list(iter_consumption_indexes(config, references))
        rollover = next(item for item in indexes if item["rollover_count"] == 1)
        previous = indexes[indexes.index(rollover) - 1]
        self.assertLess(rollover["meter_index_wh"], previous["meter_index_wh"])

    def test_sites_cannot_be_fewer_than_customers(self):
        with self.assertRaises(ValueError):
            build_reference_records(GeneratorConfig(customers=5, sites=4, include_indexes=False))

    def test_incremental_batch_continues_from_the_latest_index(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initial = root / "initial"
            write_initial_batch(GeneratorConfig(
                customers=1, sites=1, seed=3, output_dir=initial,
                as_of=datetime(2026, 1, 3, tzinfo=UTC),
            ))
            manifest = generate_incremental_batch(initial, root / "incremental", datetime(2026, 1, 4, tzinfo=UTC))
            self.assertGreater(manifest["record_counts"]["consumption_index"], 0)

    def test_late_and_corrected_events_are_versioned_and_traceable(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initial = root / "initial"
            write_initial_batch(GeneratorConfig(
                customers=1, sites=1, seed=5, output_dir=initial,
                as_of=datetime(2026, 1, 3, tzinfo=UTC),
            ))
            received_at = datetime(2026, 1, 5, tzinfo=UTC)
            generate_exception_event(initial, root / "late", "late", received_at)
            generate_exception_event(initial, root / "correction", "correction", received_at)
            late = (root / "late" / "late_consumption_index.jsonl").read_text()
            correction = (root / "correction" / "correction_consumption_index.jsonl").read_text()
            self.assertIn('"source_version": 1', late)
            self.assertIn('"source_version": 2', correction)

    def test_processor_deduplicates_a_correction_and_derives_intervals(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initial = root / "initial"
            write_initial_batch(GeneratorConfig(customers=1, sites=1, seed=5, output_dir=initial,
                as_of=datetime(2026, 1, 3, tzinfo=UTC)))
            correction = root / "correction"
            generate_exception_event(initial, correction, "correction", datetime(2026, 1, 5, tzinfo=UTC))
            result = process_indexes([initial, correction])
            self.assertGreater(len(result["consumption_intervals"]), 0)
            self.assertTrue(any(row["source_version"] == 2 for row in result["validated_indexes"]))


if __name__ == "__main__":
    unittest.main()
