import copy
import unittest

from lakehouse_platform.cleanup_guard import (
    ALLOWED_DELETE_ADDRESSES,
    DestroyPlanRejected,
    TAGGABLE_DELETE_ADDRESSES,
    validate_destroy_plan,
)


DEMO_ID = "demo-20261026"
EXPIRES_AT = "2026-10-27T21:00:00Z"


def approved_plan():
    changes = []
    for address in sorted(ALLOWED_DELETE_ADDRESSES):
        before = {}
        if address in TAGGABLE_DELETE_ADDRESSES:
            before["tags_all"] = {
                "Project": "aws-lakehouse-platform",
                "Environment": "demo",
                "DemoId": DEMO_ID,
                "AutoCleanup": "true",
                "ExpiresAt": EXPIRES_AT,
            }
        changes.append(
            {
                "address": address,
                "change": {"actions": ["delete"], "before": before},
            }
        )
    return {"resource_changes": changes}


class CleanupGuardTests(unittest.TestCase):
    def test_accepts_empty_destroy_plan_as_an_idempotent_no_op(self):
        validate_destroy_plan({"resource_changes": []}, DEMO_ID, EXPIRES_AT)

    def test_accepts_exact_expected_deletion_scope(self):
        validate_destroy_plan(approved_plan(), DEMO_ID, EXPIRES_AT)

    def test_rejects_unknown_address(self):
        plan = approved_plan()
        plan["resource_changes"].append(
            {"address": "aws_s3_bucket.unrelated", "change": {"actions": ["delete"], "before": {}}}
        )
        with self.assertRaises(DestroyPlanRejected):
            validate_destroy_plan(plan, DEMO_ID, EXPIRES_AT)

    def test_rejects_deletion_of_the_persistent_deployment_identity(self):
        plan = approved_plan()
        plan["resource_changes"].append(
            {"address": "aws_iam_user.operator", "change": {"actions": ["delete"], "before": {}}}
        )
        with self.assertRaises(DestroyPlanRejected):
            validate_destroy_plan(plan, DEMO_ID, EXPIRES_AT)

    def test_rejects_non_delete_action(self):
        plan = approved_plan()
        plan["resource_changes"][0]["change"]["actions"] = ["update"]
        with self.assertRaises(DestroyPlanRejected):
            validate_destroy_plan(plan, DEMO_ID, EXPIRES_AT)

    def test_rejects_wrong_demo_id(self):
        plan = copy.deepcopy(approved_plan())
        for change in plan["resource_changes"]:
            if change["address"] in TAGGABLE_DELETE_ADDRESSES:
                change["change"]["before"]["tags_all"]["DemoId"] = "another-demo"
                break
        with self.assertRaises(DestroyPlanRejected):
            validate_destroy_plan(plan, DEMO_ID, EXPIRES_AT)
