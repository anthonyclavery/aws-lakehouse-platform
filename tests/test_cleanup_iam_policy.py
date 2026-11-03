from pathlib import Path
import unittest
import re


POLICY_PATH = Path(__file__).parents[1] / "terraform" / "bootstrap-state" / "cleanup.tf"


class CleanupIamPolicyTests(unittest.TestCase):
    def test_delete_database_covers_catalog_database_and_all_child_resources(self):
        """AWS Glue requires DeleteDatabase on the database, catalog, tables and UDFs."""
        policy = POLICY_PATH.read_text(encoding="utf-8")

        required_resource_suffixes = (
            ":catalog",
            ":database/aws_lakehouse_platform_*",
            ":table/aws_lakehouse_platform_*/*",
            ":userDefinedFunction/aws_lakehouse_platform_*/*",
        )

        self.assertIn('actions = ["glue:DeleteDatabase", "glue:GetDatabase", "glue:GetTags", "glue:GetTable"]', policy)
        for resource_suffix in required_resource_suffixes:
            self.assertIn(resource_suffix, policy)

    def test_protected_identity_dependency_access_is_read_only(self):
        policy = POLICY_PATH.read_text()
        block = re.search(r'sid\s*=\s*"ReadProtectedDeploymentIdentity"(.*?)\n  }', policy, re.S)[1]
        self.assertIn('"iam:GetUser"', block)
        self.assertIn('"iam:GetRolePolicy"', block)
        self.assertNotIn("iam:Delete", block)
        self.assertNotIn("iam:Put", block)
        self.assertNotIn("iam:Update", block)


if __name__ == "__main__":
    unittest.main()
