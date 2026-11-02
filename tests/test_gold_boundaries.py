import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import unittest

from lakehouse_platform.pricing import PricingRejected, gold_products, iter_priced_segments, price_intervals


def references(contract="BASE"):
    validity = {"valid_from": "2020-01-01T00:00:00Z", "valid_to": None}
    rules = {
        "BASE": [("ALL_DAYS", None, None, "0.20")],
        "WEEKEND": [("WEEKDAY", None, None, "0.20"), ("WEEKEND", None, None, "0.10")],
        "PEAK_OFF_PEAK": [("ALL_DAYS", "22:30", "06:30", "0.10"),
                          ("ALL_DAYS", "06:30", "22:30", "0.20")],
    }
    return {
        "site_meter": [{**validity, "meter_id": "m", "site_id": "s"}],
        "customer_site": [{**validity, "customer_id": "c", "site_id": "s"}],
        "site_contract": [{**validity, "site_id": "s", "contract_type": contract}],
        "tariff_grid": [{**validity, "contract_type": contract, "day_scope": day,
                         "time_from_local": start, "time_to_local": end, "price_eur_per_kwh": price}
                        for day, start, end, price in rules[contract]],
    }


def interval(start="2026-01-05T00:00:00Z", hours=1, wh=1000, status="VALID"):
    start = datetime.fromisoformat(start.replace("Z", "+00:00"))
    return {"meter_id": "m", "interval_start": start, "interval_end": start + timedelta(hours=hours),
            "consumption_wh": wh, "quality_status": status}


def totals(rows):
    return tuple(sum((r[field] for r in rows), Decimal(0)) for field in ("consumption_kwh", "energy_cost_eur"))


class GoldBoundaryTests(unittest.TestCase):
    def test_base_zero_warning_and_quarantine(self):
        for status in ("VALID", "VALID_WITH_WARNING"):
            self.assertEqual((Decimal(1), Decimal("0.2")), totals(price_intervals([interval(status=status)], references())))
        self.assertEqual((Decimal(0), Decimal(0)), totals(price_intervals([interval(wh=0)], references())))
        for status in ("QUARANTINED", "VALID_BUT_UNKNOWN"):
            self.assertEqual([], price_intervals([interval(status=status)], references()))

    def test_weekend_uses_local_midnight_and_half_open_days(self):
        rows = price_intervals([interval("2026-01-09T22:30:00Z")], references("WEEKEND"))
        self.assertEqual((Decimal(1), Decimal("0.15")), totals(rows))
        daily = gold_products(rows)["daily_customer_energy"]
        self.assertEqual(["2026-01-09", "2026-01-10"], [r["local_date"] for r in daily])
        self.assertEqual([Decimal("0.10"), Decimal("0.05")], [r["energy_cost_eur"] for r in daily])
        sunday = price_intervals([interval("2026-01-11T22:30:00Z")], references("WEEKEND"))
        self.assertEqual(Decimal("0.15"), totals(sunday)[1])

    def test_off_peak_minute_boundaries_and_overnight_window(self):
        refs = references("PEAK_OFF_PEAK")
        for start, cost in (("2026-01-05T05:00:00Z", "0.15"), ("2026-01-05T21:00:00Z", "0.15"),
                            ("2026-01-05T05:30:00Z", "0.20"), ("2026-01-05T21:30:00Z", "0.10"),
                            ("2026-01-05T23:00:00Z", "0.10")):
            with self.subTest(start=start):
                self.assertEqual(Decimal(cost), totals(price_intervals([interval(start)], refs))[1])

    def test_month_boundary_is_attributed_to_consuming_day(self):
        rows = price_intervals([interval("2026-01-31T22:30:00Z")], references())
        products = gold_products(rows)
        self.assertEqual(["2026-01", "2026-02"], [r["local_month"] for r in products["monthly_customer_energy"]])
        for product in products.values():
            self.assertEqual((Decimal(1), Decimal("0.20")), totals(product))

    def test_ownership_contract_and_tariff_changes_inside_interval(self):
        for flow in ("customer_site", "site_meter", "site_contract", "tariff_grid"):
            with self.subTest(flow=flow):
                refs = references()
                old = refs[flow][0]
                change = "2026-01-05T00:17:00Z"
                old["valid_to"] = change
                new = {**old, "valid_from": change, "valid_to": None}
                if flow == "customer_site":
                    new["customer_id"] = "c2"
                elif flow == "site_meter":
                    new["site_id"] = "s2"
                    for related in ("customer_site", "site_contract"):
                        refs[related].append({**refs[related][0], "site_id": "s2"})
                elif flow == "site_contract":
                    new["contract_type"] = "WEEKEND"
                    refs["tariff_grid"].extend(references("WEEKEND")["tariff_grid"])
                else:
                    new["price_eur_per_kwh"] = "0.30"
                refs[flow].append(new)
                rows = price_intervals([interval(wh=6000)], refs)
                self.assertEqual(Decimal(6), totals(rows)[0])
                self.assertEqual(change.replace("Z", "+00:00"), rows[0]["segment_end"])
                if flow == "customer_site":
                    self.assertEqual(["c", "c2"], [r["customer_id"] for r in rows])
                if flow == "tariff_grid":
                    self.assertEqual(Decimal("1.63"), totals(rows)[1])

    def test_meter_closed_exactly_at_interval_end_remains_billable(self):
        refs = references()
        refs["site_meter"][0]["valid_to"] = "2026-01-05T01:00:00Z"
        self.assertEqual(Decimal(1), totals(price_intervals([interval()], refs))[0])

    def test_dst_days_conserve_elapsed_energy(self):
        for start, hours, day in (("2026-03-28T23:00:00Z", 23, "2026-03-29"),
                                  ("2026-10-24T22:00:00Z", 25, "2026-10-25")):
            rows = price_intervals([interval(start, hours, hours * 1000)], references())
            self.assertEqual({day}, {r["local_date"] for r in rows})
            self.assertEqual((Decimal(hours), Decimal(hours) * Decimal("0.20")), totals(rows))

    def test_dst_window_in_missing_and_repeated_hour(self):
        refs = references("PEAK_OFF_PEAK")
        refs["tariff_grid"][0].update(time_from_local="02:30", time_to_local="04:00")
        refs["tariff_grid"][1].update(time_from_local="04:00", time_to_local="02:30")
        spring = price_intervals([interval("2026-03-29T00:00:00Z", 2, 2000)], refs)
        autumn = price_intervals([interval("2026-10-25T00:00:00Z", 3, 3000)], refs)
        self.assertEqual(Decimal("0.30"), totals(spring)[1])
        self.assertEqual(Decimal("0.40"), totals(autumn)[1])

    def test_invalid_intervals_are_rejected(self):
        for hours, wh in ((0, 1), (-1, 1), (1, -1), (1, "NaN"), (1, "Infinity")):
            with self.subTest(hours=hours, wh=wh), self.assertRaises(PricingRejected):
                price_intervals([interval(hours=hours, wh=wh)], references())
        naive = interval()
        naive["interval_start"] = datetime(2026, 1, 5)
        with self.assertRaises(PricingRejected):
            price_intervals([naive], references())

    def test_missing_or_overlapping_reference_and_tariff_reject(self):
        for flow in references():
            for duplicate in (False, True):
                refs = references()
                refs[flow] = refs[flow] * 2 if duplicate else []
                with self.subTest(flow=flow, duplicate=duplicate), self.assertRaises(PricingRejected):
                    price_intervals([interval()], refs)

    def test_unknown_contract_scope_and_malformed_window_reject(self):
        for field, value in (("day_scope", "HOLIDAY"), ("price_eur_per_kwh", "-1"),
                             ("price_eur_per_kwh", "NaN"), ("time_from_local", "06:00")):
            refs = references()
            refs["tariff_grid"][0][field] = value
            with self.subTest(field=field), self.assertRaises(PricingRejected):
                price_intervals([interval()], refs)
        refs = references()
        refs["site_contract"][0]["contract_type"] = "UNKNOWN"
        with self.assertRaises(PricingRejected):
            price_intervals([interval()], refs)
        refs = references()
        refs["tariff_grid"][0].update(time_from_local="00:00", time_to_local="00:00")
        with self.assertRaises(PricingRejected):
            price_intervals([interval()], refs)

    def test_year_boundary_and_equivalent_offset_timestamps(self):
        rows = price_intervals([interval("2026-12-31T22:30:00Z")], references())
        self.assertEqual(["2026-12", "2027-01"], [r["local_month"] for r in gold_products(rows)["monthly_customer_energy"]])
        equivalent = price_intervals([interval("2026-12-31T23:30:00+01:00")], references())
        self.assertEqual(rows, equivalent)

    def test_tariff_gap_does_not_emit_partial_interval(self):
        refs = references()
        refs["tariff_grid"][0]["valid_to"] = "2026-01-05T00:30:00Z"
        stream = iter_priced_segments([interval()], refs)
        with self.assertRaises(PricingRejected):
            next(stream)

    def test_fractional_allocation_conserves_energy_and_replay_is_deterministic(self):
        source = interval("2026-01-05T00:00:01Z", hours=3, wh=1)
        rows = price_intervals([source], references())
        self.assertEqual(Decimal("0.001"), totals(rows)[0])
        self.assertEqual(rows, price_intervals([copy.deepcopy(source)], references()))

    def test_empty_input_clears_all_products(self):
        self.assertEqual({"daily_site_energy": [], "daily_customer_energy": [], "monthly_customer_energy": []}, gold_products([]))
