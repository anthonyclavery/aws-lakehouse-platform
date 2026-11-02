"""Regression checks for permissions required by the real qualification path."""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).parents[1]


class ExecutionPermissionTests(unittest.TestCase):
    def statement(self, sid):
        policy = (ROOT / "terraform/operator_identity.tf").read_text()
        match = re.search(r'Sid\s*=\s*"' + sid + r'"(.*?)\n\s*},', policy, re.S)
        self.assertIsNotNone(match)
        return match[1]

    def test_bucket_encryption_permission_is_explicit_and_project_scoped(self):
        statement = self.statement("ManageOnlyProjectS3")
        self.assertIn('"s3:PutEncryptionConfiguration"', statement)
        self.assertIn('"arn:aws:s3:::aws-lakehouse-platform-*"', statement)

    def test_qualification_warehouse_access_is_read_only(self):
        statement = self.statement("ReadOnlyProjectWarehouse")
        self.assertIn('"s3:GetObject"', statement)
        self.assertNotIn('"s3:PutObject"', statement)
        self.assertIn('"arn:aws:s3:::aws-lakehouse-platform-*/warehouse/*"', statement)

    def test_athena_permissions_are_scoped_to_project_workgroups(self):
        statement = self.statement("ManageOnlyProjectAthena")
        self.assertIn('"athena:StartQueryExecution"', statement)
        self.assertIn('"athena:StopQueryExecution"', statement)
        self.assertIn(':workgroup/aws-lakehouse-platform-*"', statement)
