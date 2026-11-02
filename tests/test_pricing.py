from datetime import UTC, datetime, timedelta
from decimal import Decimal
import unittest
from lakehouse_platform.pricing import daily_customer_energy, price_intervals


class PricingTests(unittest.TestCase):
    def test_allocates_peak_off_peak_interval_and_aggregates_customer_day(self):
        start = datetime(2026, 1, 5, 4, 30, tzinfo=UTC)
        refs = {"site_meter":[{"meter_id":"m","site_id":"s","valid_from":"2020-01-01T00:00:00Z","valid_to":None}], "customer_site":[{"customer_id":"c","site_id":"s","valid_from":"2020-01-01T00:00:00Z","valid_to":None}], "site_contract":[{"site_id":"s","contract_type":"PEAK_OFF_PEAK","valid_from":"2020-01-01T00:00:00Z","valid_to":None}], "tariff_grid":[{"contract_type":"PEAK_OFF_PEAK","valid_from":"2020-01-01T00:00:00Z","valid_to":None,"day_scope":"ALL_DAYS","time_from_local":"00:00","time_to_local":"06:00","price_eur_per_kwh":"0.10"},{"contract_type":"PEAK_OFF_PEAK","valid_from":"2020-01-01T00:00:00Z","valid_to":None,"day_scope":"ALL_DAYS","time_from_local":"06:00","time_to_local":"00:00","price_eur_per_kwh":"0.20"}]}
        rows = price_intervals([{"meter_id":"m","interval_start":start,"interval_end":start + timedelta(hours=1),"consumption_wh":1000,"quality_status":"VALID"}], refs)
        self.assertEqual(Decimal("0.15"), rows[0]["energy_cost_eur"])
        self.assertEqual(Decimal("1"), daily_customer_energy(rows)[0]["consumption_kwh"])
