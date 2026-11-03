import copy
import unittest
from analyst_fixtures import analyst_before
from lakehouse_platform.analyst_access import access_addresses

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
        if address in {"aws_iam_role.ingestion[0]", "aws_iam_role_policy.ingestion[0]"}:
            before = {"name": f"aws-lakehouse-platform-{DEMO_ID}-ingestion", "role": f"aws-lakehouse-platform-{DEMO_ID}-ingestion"}
        if address in access_addresses():
            before = analyst_before(address, DEMO_ID)
        if address == "aws_iam_role_policy.glue_silver":
            before = {"role":"aws-lakehouse-platform-demo-glue-silver", "name":"aws-lakehouse-platform-demo-glue-silver"}
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
    def test_benchmark_artifacts_require_tags_and_exact_instance_names(self):
        for change in approved_plan()["resource_changes"]:
            if change["address"].startswith("aws_s3_object.benchmark_script"):
                change["change"]["before"] = {}
                with self.subTest(address=change["address"]), self.assertRaises(DestroyPlanRejected):
                    validate_destroy_plan({"resource_changes": [change]}, DEMO_ID, EXPIRES_AT)
        with self.assertRaises(DestroyPlanRejected):
            validate_destroy_plan({"resource_changes": [{"address": 'aws_s3_object.benchmark_script["unrelated"]',
                "change": {"actions": ["delete"], "before": {}}}]}, DEMO_ID, EXPIRES_AT)

    def test_glue_execution_policy_requires_exact_role_and_policy_name(self):
        expected = "aws-lakehouse-platform-demo-glue-silver"
        for before in ({}, {"role":"unrelated", "name":expected}, {"role":expected, "name":"unrelated"}):
            with self.subTest(before=before), self.assertRaises(DestroyPlanRejected):
                validate_destroy_plan({"resource_changes":[{"address":"aws_iam_role_policy.glue_silver", "change":{"actions":["delete"],"before":before}}]}, DEMO_ID, EXPIRES_AT)

    def test_athena_workgroup_requires_demo_tags(self):
        with self.assertRaises(DestroyPlanRejected):
            validate_destroy_plan({"resource_changes":[{"address":"aws_athena_workgroup.demo", "change":{"actions":["delete"],"before":{}}}]}, DEMO_ID, EXPIRES_AT)

    def test_reference_job_and_script_cannot_be_deleted_without_tags(self):
        for change in approved_plan()["resource_changes"]:
            if change["address"] in {"aws_glue_job.silver_process_reference", "aws_s3_object.silver_process_reference_script"}:
                change["change"]["before"] = {}
                with self.subTest(address=change["address"]), self.assertRaises(DestroyPlanRejected):
                    validate_destroy_plan({"resource_changes": [change]}, DEMO_ID, EXPIRES_AT)

    def test_accepts_empty_destroy_plan_as_an_idempotent_no_op(self):
        validate_destroy_plan({"resource_changes": []}, DEMO_ID, EXPIRES_AT)

    def test_accepts_real_terraform_12_targeted_noop_without_resource_changes(self):
        validate_destroy_plan({"format_version":"1.2", "applyable":False,
                               "complete":False, "errored":False}, DEMO_ID, EXPIRES_AT)

    def test_missing_resource_changes_requires_explicit_nonapplyable_nonerror_metadata(self):
        valid = {"format_version":"1.2", "applyable":False, "complete":False, "errored":False}
        invalid = [{}, {**valid,"applyable":True}, {**valid,"errored":True}, {**valid,"complete":None},
                   {**valid,"format_version":"unknown"}, {**valid,"resource_changes":None}]
        for plan in invalid:
            with self.subTest(plan=plan), self.assertRaises(DestroyPlanRejected):
                validate_destroy_plan(plan, DEMO_ID, EXPIRES_AT)

    def test_accepts_exact_expected_deletion_scope(self):
        validate_destroy_plan(approved_plan(), DEMO_ID, EXPIRES_AT)

    def test_accepts_a_tagged_subset_after_a_partial_cleanup_failure(self):
        plan = approved_plan()
        plan["resource_changes"] = [
            change
            for change in plan["resource_changes"]
            if change["address"] == "aws_glue_catalog_database.lakehouse"
        ]
        validate_destroy_plan(plan, DEMO_ID, EXPIRES_AT)

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
