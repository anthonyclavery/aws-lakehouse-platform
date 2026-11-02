from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lakehouse_platform.generator import GeneratorConfig, write_initial_batch
from lakehouse_platform.reference_processing import ReferenceDataRejected, process_reference_data


class ReferenceProcessingTests(unittest.TestCase):
    def test_processes_all_reference_flows(self):
        with TemporaryDirectory() as directory:
            batch = Path(directory) / "initial"
            write_initial_batch(GeneratorConfig(customers=2, sites=3, include_indexes=False, output_dir=batch))
            result = process_reference_data([batch])
        self.assertEqual(3, len(result["site_meter"]))
        self.assertEqual(6, len(result["tariff_grid"]))

    def test_rejects_two_active_meters_for_one_site(self):
        rows = [
            {"site_id": "site", "valid_from": "2026-01-01T00:00:00Z", "valid_to": None, "source_version": 1, "received_at": "2026-01-01T00:00:00Z"},
            {"site_id": "site", "valid_from": "2026-02-01T00:00:00Z", "valid_to": None, "source_version": 1, "received_at": "2026-02-01T00:00:00Z"},
        ]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference"
            reference.mkdir()
            (reference / "site_meter.jsonl").write_text("\n".join(__import__("json").dumps(row) for row in rows), encoding="utf-8")
            with self.assertRaises(ReferenceDataRejected):
                process_reference_data([root])
