from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import Mock

from lakehouse_platform.guarded_destroy import destroy, lf_revoke_only_failure, validate_recovery
from test_cleanup_guard import approved_plan, DEMO_ID, EXPIRES_AT


def completed(code=0, output=""):
    return CompletedProcess([], code, output, "")


class GuardedDestroyTests(unittest.TestCase):
    def subset(self, address):
        return {"resource_changes": [r for r in approved_plan()["resource_changes"] if r["address"] == address]}

    def execute(self, values):
        run = Mock(side_effect=values)
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            result = destroy("eu-west-3", DEMO_ID, EXPIRES_AT, directory=Path(folder), run=run)
        return result, run

    def test_success_uses_the_exact_allowlist_as_targets(self):
        result, run = self.execute([completed(), completed(output=json.dumps(approved_plan())), completed()])
        self.assertEqual(1, result["passes"])
        targets = {v.removeprefix("-target=") for v in run.call_args_list[0].args[0] if v.startswith("-target=")}
        self.assertEqual({r["address"] for r in approved_plan()["resource_changes"]}, targets)
        self.assertNotIn("aws_iam_user.operator", targets)

    def test_lf_partial_effect_requires_refresh_and_a_new_validated_smaller_plan(self):
        remaining = self.subset("aws_s3_bucket.lakehouse")
        result, run = self.execute([completed(), completed(output=json.dumps(approved_plan())),
            completed(1, "Error: unable to revoke LakeFormation Permissions: AccessDenied"),
            completed(), completed(output=json.dumps(remaining)), completed()])
        self.assertEqual(2, result["passes"])
        self.assertEqual(6, run.call_count)

    def test_unknown_or_mixed_failure_never_retries(self):
        for output in ("Error: deleting IAM role: AccessDenied", "AccessDenied", "Error: unable to revoke LakeFormation Permissions\nError: other failure"):
            self.assertFalse(lf_revoke_only_failure(output))
            with self.assertRaises(RuntimeError):
                self.execute([completed(), completed(output=json.dumps(approved_plan())), completed(1, output)])

    def test_remaining_lf_grants_no_progress_and_new_resources_block_retry(self):
        original = approved_plan()
        for refreshed in (original, self.subset('aws_lakeformation_permissions.cleanup_gold["daily_site_energy"]'),
                          {"resource_changes": [{"address": "unexpected", "change": {"actions": ["delete"]}}]}):
            with self.assertRaises(RuntimeError):
                validate_recovery(original, refreshed)

    def test_second_failure_is_terminal(self):
        with self.assertRaises(RuntimeError):
            self.execute([completed(), completed(output=json.dumps(approved_plan())),
                completed(1, "Error: unable to revoke LakeFormation Permissions: AccessDenied"),
                completed(), completed(output=json.dumps(self.subset("aws_s3_bucket.lakehouse"))), completed(1, "Error: other failure")])

    def test_unapproved_plan_is_never_applied(self):
        invalid = {"resource_changes": [{"address": "aws_iam_user.operator", "change": {"actions": ["delete"], "before": {}}}]}
        with self.assertRaises(ValueError):
            self.execute([completed(), completed(output=json.dumps(invalid))])
