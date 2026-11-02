from datetime import UTC, datetime
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from lakehouse_platform.generator import GeneratorConfig, build_reference_records

ROOT = Path(__file__).parents[1]


class GoldPipelineTests(unittest.TestCase):
    def test_cli_all_contracts_replay_correction_and_removed_ownership(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            initial, correction, output = root / "initial", root / "correction", root / "gold"
            (initial / "reference").mkdir(parents=True)
            refs = build_reference_records(GeneratorConfig(customers=5, sites=10, include_indexes=False,
                as_of=datetime(2026, 10, 16, tzinfo=UTC)))
            self.assertEqual({"BASE", "WEEKEND", "PEAK_OFF_PEAK"}, {r["contract_type"] for r in refs["site_contract"]})
            for flow, rows in refs.items():
                (initial / "reference" / f"{flow}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            indexes = []
            for meter in refs["site_meter"]:
                for hour, value in ((0, 1000), (1, 2000), (2, 3000)):
                    indexes.append({"meter_id": meter["meter_id"], "reading_at": f"2026-10-16T0{hour}:00:00Z",
                        "meter_index_wh": value, "rollover_count": 0, "reading_context": "NORMAL", "source_version": 1,
                        "received_at": "2026-10-17T00:00:00Z"})
            (initial / "consumption_index.jsonl").write_text("".join(json.dumps(r) + "\n" for r in indexes))

            def run(*batches):
                result = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_local_gold.py"),
                    *map(str, batches), "--output-dir", str(output)], capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
                return {p.name: p.read_text() for p in output.glob("*.jsonl")}

            first = run(initial)
            self.assertEqual(first, run(initial, initial))
            corrected = {**indexes[2], "meter_index_wh": 8000, "source_version": 2}
            correction.mkdir()
            (correction / "consumption_index.jsonl").write_text(json.dumps(corrected) + "\n")
            updated = run(initial, correction)
            self.assertNotEqual(first, updated)
            self.assertEqual(updated, run(initial, correction, correction))
            segment_cost = sum(Decimal(json.loads(line)["energy_cost_eur"])
                               for line in updated["priced_intervals.jsonl"].splitlines())
            for product in ("daily_site_energy", "daily_customer_energy", "monthly_customer_energy"):
                rows = [json.loads(line) for line in updated[f"{product}.jsonl"].splitlines()]
                self.assertEqual(Decimal(25), sum(Decimal(r["consumption_kwh"]) for r in rows))
                self.assertEqual(segment_cost, sum(Decimal(r["energy_cost_eur"]) for r in rows))

            # A reference correction transfers ownership: the obsolete customer
            # must disappear from rebuilt products, including an empty rebuild.
            old_customer = refs["customer_site"][0]["customer_id"]
            changed = [{**r, "customer_id": "replacement", "source_version": 2}
                       for r in refs["customer_site"] if r["customer_id"] == old_customer]
            (correction / "reference").mkdir()
            (correction / "reference" / "customer_site.jsonl").write_text("".join(json.dumps(r) + "\n" for r in changed))
            moved = run(initial, correction)
            customers = [json.loads(line)["customer_id"] for line in moved["daily_customer_energy.jsonl"].splitlines()]
            self.assertNotIn(old_customer, customers)
            (initial / "consumption_index.jsonl").write_text("")
            empty = run(initial)
            self.assertTrue(all(content == "" for content in empty.values()))
