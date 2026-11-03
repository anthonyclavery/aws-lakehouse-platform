import unittest
from unittest.mock import Mock, patch

from lakehouse_platform.glue_runner import GlueInvocationRejected, build_gold_arguments, run_gold_jobs


class GoldRunnerTests(unittest.TestCase):
    def test_table_arguments_are_separate_and_database_is_validated(self):
        price, aggregate = build_gold_arguments("synthetic_demo")
        self.assertEqual("glue_catalog.synthetic_demo.priced_intervals", price["PRICED_INTERVAL_TABLE"])
        self.assertEqual(price["PRICED_INTERVAL_TABLE"], aggregate["PRICED_INTERVAL_TABLE"])
        self.assertEqual(9, len(price))
        self.assertEqual(price, aggregate)
        for invalid in ("", "db.table", "a;DROP TABLE", "db-name", "Upper", "a/b"):
            with self.subTest(database=invalid), self.assertRaises(GlueInvocationRejected):
                build_gold_arguments(invalid)

    @patch("time.sleep")
    def test_aggregation_starts_only_after_successful_pricing(self, sleep):
        glue = Mock()
        glue.start_job_run.side_effect = [{"JobRunId": "price-run"}, {"JobRunId": "aggregate-run"}]
        glue.get_job_run.side_effect = [
            {"JobRun": {"JobRunState": state}} for state in ("WAITING", "RUNNING", "SUCCEEDED", "SUCCEEDED")]
        runs = run_gold_jobs(glue, "price", "aggregate", "demo", batch_id="gold-test")
        self.assertEqual(["price-run", "aggregate-run"], runs)
        self.assertEqual(["price", "aggregate"], [call.kwargs["JobName"] for call in glue.start_job_run.call_args_list])
        self.assertEqual(["price", "price", "price", "aggregate"], [call.kwargs["JobName"] for call in glue.get_job_run.call_args_list])
        self.assertEqual(2, sleep.call_count)

    def test_all_unsuccessful_terminal_states_block_aggregation(self):
        for state in ("FAILED", "STOPPED", "TIMEOUT", "ERROR", "EXPIRED"):
            glue = Mock()
            glue.start_job_run.return_value = {"JobRunId": "run"}
            glue.get_job_run.return_value = {"JobRun": {"JobRunState": state}}
            with self.subTest(state=state), self.assertRaises(RuntimeError):
                run_gold_jobs(glue, "price", "aggregate", "demo", batch_id="gold-test")
            glue.start_job_run.assert_called_once()

    def test_wait_timeout_blocks_aggregation(self):
        glue = Mock()
        glue.start_job_run.return_value = {"JobRunId": "run"}
        glue.get_job_run.return_value = {"JobRun": {"JobRunState": "RUNNING"}}
        with self.assertRaises(TimeoutError):
            run_gold_jobs(glue, "price", "aggregate", "demo", batch_id="gold-test", wait_seconds=0)
        glue.start_job_run.assert_called_once()

    def test_aws_start_failure_propagates_without_second_job(self):
        glue = Mock()
        glue.start_job_run.side_effect = RuntimeError("AWS unavailable")
        with self.assertRaises(RuntimeError):
            run_gold_jobs(glue, "price", "aggregate", "demo", batch_id="gold-test")
        glue.start_job_run.assert_called_once()

    def test_missing_refresh_identity_is_rejected_before_aws(self):
        glue = Mock()
        with self.assertRaises(GlueInvocationRejected):
            run_gold_jobs(glue, "price", "aggregate", "demo")
        glue.start_job_run.assert_not_called()
