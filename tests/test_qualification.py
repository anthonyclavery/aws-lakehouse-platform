from decimal import Decimal
import unittest
from unittest.mock import Mock

from lakehouse_platform.qualification import compare_product, query_rows


class QualificationTests(unittest.TestCase):
    def test_compares_every_group_and_both_measures(self):
        row = {"customer_id":"c", "local_date":"2026-01-01", "consumption_kwh":"1.000000000000", "energy_cost_eur":"0.2"}
        self.assertTrue(compare_product("daily_customer_energy", [row], [row])["passed"])
        for field in ("consumption_kwh", "energy_cost_eur"):
            actual = {**row, field:"9"}
            self.assertFalse(compare_product("daily_customer_energy", [row], [actual])["passed"])
        self.assertFalse(compare_product("daily_customer_energy", [row], [])["passed"])
        self.assertFalse(compare_product("daily_customer_energy", [], [row])["passed"])

    def test_duplicate_and_nonfinite_groups_are_rejected(self):
        row = {"customer_id":"c", "local_date":"2026-01-01", "consumption_kwh":"1", "energy_cost_eur":"0.2"}
        with self.assertRaises(ValueError):
            compare_product("daily_customer_energy", [row], [row,row])
        for value in ("NaN", "Infinity"):
            with self.assertRaises(ValueError):
                compare_product("daily_customer_energy", [row], [{**row,"energy_cost_eur":value}])

    def test_precision_tolerance_is_explicit(self):
        row = {"customer_id":"c", "local_date":"2026-01-01", "consumption_kwh":"1", "energy_cost_eur":"0.2"}
        actual = {**row,"energy_cost_eur":"0.200000000001"}
        self.assertTrue(compare_product("daily_customer_energy", [row], [actual])["passed"])
        self.assertFalse(compare_product("daily_customer_energy", [row], [actual], tolerance=Decimal(0))["passed"])

    def test_athena_pagination_skips_only_the_first_header(self):
        athena = Mock()
        athena.start_query_execution.return_value = {"QueryExecutionId":"query"}
        athena.get_query_execution.return_value = {"QueryExecution":{"Status":{"State":"SUCCEEDED"},"Statistics":{"DataScannedInBytes":3}}}
        metadata = {"ColumnInfo":[{"Name":"value"}]}
        athena.get_query_results.side_effect = [
            {"ResultSet":{"ResultSetMetadata":metadata,"Rows":[{"Data":[{"VarCharValue":"value"}]},{"Data":[{"VarCharValue":"first"}]}]},"NextToken":"page2"},
            {"ResultSet":{"ResultSetMetadata":metadata,"Rows":[{"Data":[{"VarCharValue":"second"}]}]}}]
        rows, statistics = query_rows(athena,"demo_db","workgroup","SELECT 1")
        self.assertEqual([{"value":"first"},{"value":"second"}],rows)
        self.assertEqual(3,statistics["DataScannedInBytes"])
        self.assertEqual({"ResultReuseByAgeConfiguration": {"Enabled": False}},
                         athena.start_query_execution.call_args.kwargs["ResultReuseConfiguration"])
        self.assertEqual("page2",athena.get_query_results.call_args_list[1].kwargs["NextToken"])

    def test_query_failure_and_timeout_fail_without_reading_results(self):
        for state in ("FAILED","CANCELLED","RUNNING"):
            athena=Mock()
            athena.start_query_execution.return_value={"QueryExecutionId":"query"}
            athena.get_query_execution.return_value={"QueryExecution":{"Status":{"State":state}}}
            with self.subTest(state=state),self.assertRaises((RuntimeError,TimeoutError)):
                query_rows(athena,"demo_db","workgroup","SELECT 1",timeout_seconds=0)
            athena.get_query_results.assert_not_called()
            if state=="RUNNING":
                athena.stop_query_execution.assert_called_once_with(QueryExecutionId="query")
