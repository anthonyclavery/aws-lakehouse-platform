from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lakehouse_platform.generator import GeneratorConfig, build_reference_records, iter_consumption_indexes, write_initial_batch


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


if __name__ == "__main__":
    unittest.main()
