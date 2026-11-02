from pathlib import Path
import unittest

from lakehouse_platform.cleanup_guard import ALLOWED_DELETE_ADDRESSES, DestroyPlanRejected, validate_destroy_plan
from test_cleanup_guard import approved_plan, DEMO_ID, EXPIRES_AT

ROOT = Path(__file__).parents[1]


class GoldCleanupTests(unittest.TestCase):
    def test_each_gold_resource_requires_demo_tags(self):
        for change in approved_plan()["resource_changes"]:
            if ".gold" in change["address"]:
                change["change"]["before"] = {}
                with self.subTest(address=change["address"]), self.assertRaises(DestroyPlanRejected):
                    validate_destroy_plan({"resource_changes": [change]}, DEMO_ID, EXPIRES_AT)

    def test_unknown_gold_instance_cannot_be_deleted(self):
        address = 'aws_glue_job.gold["unrelated"]'
        self.assertNotIn(address, ALLOWED_DELETE_ADDRESSES)
        with self.assertRaises(DestroyPlanRejected):
            validate_destroy_plan({"resource_changes": [{"address": address, "change": {"actions": ["delete"], "before": {}}}]}, DEMO_ID, EXPIRES_AT)

    def test_job_deletion_permission_is_not_in_cleanup_trust_policy(self):
        policy = (ROOT / "terraform" / "bootstrap-state" / "cleanup.tf").read_text()
        trust, permissions = policy.split('data "aws_iam_policy_document" "cleanup_build" {', 1)
        self.assertNotIn("glue:DeleteJob", trust)
        self.assertIn("glue:DeleteJob", permissions)
        self.assertIn('"gold-price", "gold-aggregate"', permissions)
