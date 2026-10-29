from pathlib import Path
import unittest


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

        self.assertIn('actions = ["glue:DeleteDatabase", "glue:GetDatabase", "glue:GetTags"]', policy)
        for resource_suffix in required_resource_suffixes:
            self.assertIn(resource_suffix, policy)


if __name__ == "__main__":
    unittest.main()
