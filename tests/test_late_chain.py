from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from lakehouse_platform.generator import stable_uuid
from lakehouse_platform.integrated_pipeline import build_integrated_invocations, run_integrated_batch
from lakehouse_platform.landing import plan_landing_upload
from lakehouse_platform.late_chain_fixture import write_late_chain_fixture
from lakehouse_platform.processing import process_indexes
from lakehouse_platform.qualification import expected_products
from test_integrated_pipeline import VALUES
from test_landing import FakeS3


class LateChainTests(TestCase):
    def test_late_and_correction_conserve_energy_but_change_tariff_allocation(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "fixture"
            summary = write_late_chain_fixture(root)
            self.assertFalse((root / "append").exists())
            history = []
            for phase in ("initial", "late", "correction"):
                history.append(root / phase)
                expected = summary["phases"][phase]
                silver = process_indexes(history)
                self.assertEqual(expected["indexes"], len(silver["validated_indexes"]))
                self.assertEqual(expected["intervals"], len(silver["consumption_intervals"]))
                self.assertEqual(30000, sum(row["consumption_wh"] for row in silver["consumption_intervals"]))
                for rows in expected_products(history).values():
                    self.assertEqual(Decimal(expected["rounded_gold_kwh"]), sum(row["consumption_kwh"] for row in rows))
                    cost = sum(row["energy_cost_eur"] for row in rows)
                    self.assertEqual(Decimal(expected["rounded_gold_eur"]), cost)
                    ratio = Fraction(expected["exact_cost_fraction"])
                    self.assertLessEqual(abs(cost - Decimal(ratio.numerator) / Decimal(ratio.denominator)), Decimal("1e-11"))
                contract_costs = {"initial": ("2", "13/10", "7/5"), "late": ("2", "7/5", "48/35"),
                                  "correction": ("2", "7/5", "103/70")}[phase]
                monthly = {row["customer_id"]: row["energy_cost_eur"] for row in expected_products(history)["monthly_customer_energy"]}
                for meter, fraction in enumerate(contract_costs):
                    ratio = Fraction(fraction)
                    self.assertLessEqual(abs(monthly[stable_uuid("integrated-customer", meter)] - Decimal(ratio.numerator) / Decimal(ratio.denominator)), Decimal("1e-11"))

    def test_correction_changes_only_its_endpoint_and_successor(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "fixture"
            write_late_chain_fixture(root)
            initial, late, correction = [root / name for name in ("initial", "late", "correction")]
            before = process_indexes([initial, late])
            after = process_indexes([initial, late, correction])
            rows = lambda result: {(r["meter_id"], r["interval_end"]): r for r in result["consumption_intervals"]}
            old, new = rows(before), rows(after)
            changed = [key for key in new if old[key] != new[key]]
            self.assertEqual(6, len(changed))
            self.assertEqual({"2026-01-09T22:00:00Z", "2026-01-09T23:00:00Z"}, {key[1] for key in changed})
            self.assertEqual(3, sum(int(r["source_version"]) == 2 for r in after["validated_indexes"]))

    def test_unchanged_and_older_replays_keep_full_rows_versions_and_products(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / "fixture"
            write_late_chain_fixture(root)
            history = [root / n for n in ("initial", "late", "correction")]
            for replay in (root / "correction", root / "initial"):
                self.assertEqual(process_indexes(history), process_indexes([*history, replay]))
                self.assertEqual(expected_products(history), expected_products([*history, replay]))
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(6, len(plan_landing_upload(root / "initial", "late-initial")))
            self.assertEqual(2, len(plan_landing_upload(root / "late", "late-event")))
            self.assertEqual(before, {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_replay_uses_original_bronze_and_silver_id_but_fresh_gold_id(self):
        stages = build_integrated_invocations(VALUES, "late-initial", load_references=False, gold_batch_id="old-replay-fresh")
        self.assertEqual("late-initial", stages[0][2]["BATCH_ID"])
        self.assertTrue(stages[0][2]["LANDING_PATH"].endswith("batch_id=late-initial/consumption_index.jsonl"))
        self.assertEqual(["old-replay-fresh"] * 2, [s[2]["BATCH_ID"] for s in stages[1:]])
        with self.assertRaises(ValueError):
            build_integrated_invocations(VALUES, "late-initial", load_references=False, gold_batch_id="../invalid")

    def test_settling_delay_only_between_reuses_of_the_same_completed_job(self):
        with TemporaryDirectory() as folder, patch("lakehouse_platform.integrated_pipeline.time.sleep") as sleep:
            root = Path(folder) / "fixture"
            write_late_chain_fixture(root)
            glue = Mock()
            glue.start_job_run.return_value = {"JobRunId": "synthetic"}
            glue.get_job_run.return_value = {"JobRun": {"JobRunState": "SUCCEEDED"}}
            run_integrated_batch(FakeS3(), glue, VALUES, root / "initial", "late-initial", load_references=True,
                                 journal=Path(folder) / "journal", poll_seconds=0, reuse_delay_seconds=30)
            self.assertEqual(3, sleep.call_count)
            self.assertTrue(all(call.args == (30,) for call in sleep.call_args_list))
