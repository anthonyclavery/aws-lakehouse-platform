from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from lakehouse_platform.generator import stable_uuid
from lakehouse_platform.integrated_fixture import write_integrated_fixture
from lakehouse_platform.integrated_pipeline import build_integrated_invocations, run_integrated_batch
from lakehouse_platform.landing import plan_landing_upload
from lakehouse_platform.processing import process_indexes
from lakehouse_platform.qualification import expected_products
from lakehouse_platform.integrated_validation import compare_silver_rows
from test_landing import FakeS3

VALUES = {"landing_path": "s3://aws-lakehouse-platform-integrated-test-aabbccdd/landing/",
          "glue_database_name": "aws_lakehouse_platform_integrated_test",
          "silver_reference_job_name": "aws-lakehouse-platform-integrated-test-silver-process-reference",
          "gold_job_names": {name: "aws-lakehouse-platform-integrated-test-gold-" + name for name in ("price", "aggregate")}}


class IntegratedPipelineTests(TestCase):
    def test_source_fixture_crosses_tariff_boundaries_and_matches_hand_calculation(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "fixture"
            write_integrated_fixture(root)
            self.assertEqual(6, len(plan_landing_upload(root / "initial", "integrated-initial")))
            self.assertEqual(2, len(plan_landing_upload(root / "append", "integrated-append")))
            for names, indexes, intervals, energy, cost in ((["initial"], 9, 6, "30", "4.70"),
                                                           (["initial", "append"], 12, 9, "33", "5.30")):
                batches = [root / name for name in names]
                silver = process_indexes(batches)
                self.assertEqual(indexes, len(silver["validated_indexes"]))
                self.assertEqual(intervals, len(silver["consumption_intervals"]))
                self.assertFalse(silver["quarantine"])
                products = expected_products(batches)
                self.assertEqual([6, 6, 3], [len(products[t]) for t in ("daily_site_energy", "daily_customer_energy", "monthly_customer_energy")])
                for rows in products.values():
                    self.assertEqual(Decimal(energy), sum(r["consumption_kwh"] for r in rows))
                    self.assertEqual(Decimal(cost), sum(r["energy_cost_eur"] for r in rows))
            monthly = expected_products([root / "initial"])["monthly_customer_energy"]
            by_customer = {row["customer_id"]: row["energy_cost_eur"] for row in monthly}
            self.assertEqual([Decimal("2.00"), Decimal("1.30"), Decimal("1.40")],
                             [by_customer[stable_uuid("integrated-customer", i)] for i in range(3)])
            daily = expected_products([root / "initial"])["daily_site_energy"]
            for index, costs in enumerate((("0.60", "1.40"), ("0.60", "0.70"), ("0.50", "0.90"))):
                rows = sorted((r for r in daily if r["customer_id"] == stable_uuid("integrated-customer", index)), key=lambda r: r["local_date"])
                self.assertEqual([Decimal(c) for c in costs], [r["energy_cost_eur"] for r in rows])
                self.assertEqual([Decimal("3"), Decimal("7")], [r["consumption_kwh"] for r in rows])

    def test_silver_comparison_normalizes_timestamp_encoding_but_preserves_versions_and_duplicates(self):
        expected = [{"reading_at": "2026-01-09T20:00:00Z", "source_version": 1}]
        actual = [{"reading_at": "2026-01-09 20:00:00.000", "source_version": "1"}]
        fields = ("reading_at", "source_version")
        self.assertTrue(compare_silver_rows(expected, actual, fields)["passed"])
        self.assertFalse(compare_silver_rows(expected, actual * 2, fields)["passed"])
        actual[0]["source_version"] = "2"
        self.assertFalse(compare_silver_rows(expected, actual, fields)["passed"])

    def test_fixture_is_deterministic_and_refuses_overwrite(self):
        with TemporaryDirectory() as folder:
            first, second = [Path(folder) / n for n in ("one", "two")]
            write_integrated_fixture(first)
            write_integrated_fixture(second)
            for path in first.rglob("*"):
                if path.is_file(): self.assertEqual(path.read_bytes(), (second / path.relative_to(first)).read_bytes())
            with self.assertRaises(FileExistsError): write_integrated_fixture(first)

    def test_invocations_keep_exact_source_files_tables_and_order(self):
        initial = build_integrated_invocations(VALUES, "integrated-initial", load_references=True)
        self.assertEqual(["reference:site_meter", "reference:customer_site", "reference:site_contract", "reference:tariff_grid",
                          "silver:indexes", "gold:price", "gold:aggregate"], [s[0] for s in initial])
        self.assertTrue(initial[4][2]["LANDING_PATH"].endswith("/batch_id=integrated-initial/consumption_index.jsonl"))
        self.assertEqual(["silver:indexes", "gold:price", "gold:aggregate"],
                         [s[0] for s in build_integrated_invocations(VALUES, "integrated-append", load_references=False)])
        self.assertTrue(all("scriptLocation" not in args for _, _, args in initial))

    def test_refuses_cross_demo_outputs_and_unsafe_batch_before_launch(self):
        for key, value in (("landing_path", "s3://other/landing/"), ("glue_database_name", "other_db"),
                           ("silver_reference_job_name", "unrelated"), ("gold_job_names", {"price": "unrelated"})):
            values = deepcopy(VALUES)
            values[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                build_integrated_invocations(values, "integrated-initial", load_references=True)
        with self.assertRaises(ValueError):
            build_integrated_invocations(VALUES, "../escape", load_references=False)

    def test_missing_reference_input_blocks_ingestion_and_all_jobs(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "fixture"
            write_integrated_fixture(root)
            s3, glue = FakeS3(), Mock()
            with self.assertRaises(ValueError):
                run_integrated_batch(s3, glue, VALUES, root / "append", "integrated-initial", load_references=True, journal=Path(folder) / "jobs.jsonl")
            self.assertFalse(s3.objects)
            glue.start_job_run.assert_not_called()

    def test_silver_failure_stops_gold_and_keeps_private_failure_journal(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "fixture"
            write_integrated_fixture(root)
            glue = Mock()
            glue.start_job_run.return_value = {"JobRunId": "synthetic-run"}
            glue.get_job_run.side_effect = [{"JobRun": {"JobRunState": s}} for s in ["SUCCEEDED"] * 4 + ["FAILED"]]
            journal = Path(folder) / "jobs.jsonl"
            with self.assertRaises(RuntimeError):
                run_integrated_batch(FakeS3(), glue, VALUES, root / "initial", "integrated-initial", load_references=True, journal=journal, poll_seconds=0)
            self.assertEqual(5, glue.start_job_run.call_count)
            last = json.loads(journal.read_text().splitlines()[-1])
            self.assertEqual("silver:indexes", last["stage"])
            self.assertEqual("FAILED", last["state"])

    def test_source_failure_prevents_every_glue_launch(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "fixture"
            write_integrated_fixture(root)
            s3, glue = Mock(), Mock()
            s3.put_object.side_effect = OSError("upload interrupted")
            with self.assertRaises(OSError):
                run_integrated_batch(s3, glue, VALUES, root / "initial", "integrated-initial", load_references=True, journal=Path(folder) / "jobs.jsonl")
            glue.start_job_run.assert_not_called()
