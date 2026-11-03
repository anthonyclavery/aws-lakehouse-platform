from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock
from lakehouse_platform.analyst_access import GOLD
from lakehouse_platform.replay_validation import REPLAY_TABLES, SILVER_TABLES, capture_replay_metadata, compare_replay


class ReplayValidationTests(TestCase):
    def evidence(self):
        silver = {"passed": True, "tables": {t: {"actual_rows": 3, "actual_sha256": "a" * 64} for t in SILVER_TABLES}}
        analyst = {"passed": True, "results_reused": False, "temporary_credentials_used": True, "gold_select_restored": True,
                   "gold_products": {t: {"actual_rows": 3, "actual_sha256": "a" * 64} for t in GOLD}}
        metadata = {t: "a" * 64 for t in REPLAY_TABLES}
        return silver, analyst, metadata

    def test_identical_full_content_and_metadata_pass(self):
        silver, analyst, metadata = self.evidence()
        self.assertTrue(compare_replay(silver, deepcopy(silver), analyst, deepcopy(analyst), metadata, deepcopy(metadata))["passed"])

    def test_same_counts_cannot_hide_version_cost_or_metadata_changes(self):
        silver, analyst, metadata = self.evidence()
        after = deepcopy(silver)
        after["tables"]["consumption_index"]["actual_sha256"] = "b" * 64
        self.assertFalse(compare_replay(silver, after, analyst, analyst, metadata, metadata)["passed"])
        after = deepcopy(analyst)
        after["gold_products"]["daily_site_energy"]["actual_sha256"] = "b" * 64
        self.assertFalse(compare_replay(silver, silver, analyst, after, metadata, metadata)["passed"])
        after_meta = {**metadata, "priced_intervals": "b" * 64}
        self.assertFalse(compare_replay(silver, silver, analyst, analyst, metadata, after_meta)["passed"])

    def test_incomplete_failed_or_reused_evidence_is_not_accepted(self):
        silver, analyst, metadata = self.evidence()
        for before in ({**silver, "passed": False}, {**silver, "tables": {}}):
            with self.assertRaises(ValueError): compare_replay(before, silver, analyst, analyst, metadata, metadata)
        for before in ({**analyst, "results_reused": True}, {**analyst, "temporary_credentials_used": False}, {**analyst, "gold_products": {}}):
            with self.assertRaises(ValueError): compare_replay(silver, silver, before, analyst, metadata, metadata)
        with self.assertRaises(ValueError): compare_replay(silver, silver, analyst, analyst, {}, metadata)

    def test_capture_refuses_missing_or_foreign_metadata_instead_of_counting_null_as_stable(self):
        glue = Mock()
        database = "aws_lakehouse_platform_demo_test"
        bucket = "aws-lakehouse-platform-demo-test-aabbccdd"
        for params in ({}, {"table_type": "ICEBERG", "metadata_location": "s3://other/warehouse/m.json"}):
            glue.get_table.return_value = {"Table": {"Parameters": params}}
            with self.assertRaises(ValueError): capture_replay_metadata(glue, database, bucket)
        glue.get_table.return_value = {"Table": {"Parameters": {"table_type": "ICEBERG", "metadata_location": f"s3://{bucket}/warehouse/t/metadata/m.json"}}}
        self.assertEqual(REPLAY_TABLES, set(capture_replay_metadata(glue, database, bucket)))
