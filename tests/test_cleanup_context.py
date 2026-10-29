import copy
import unittest

from lakehouse_platform.cleanup_context import CleanupContextRejected, derive_cleanup_context


def approved_state():
    return {
        "resources": [
            {
                "type": "terraform_data",
                "name": "cleanup_context",
                "instances": [
                    {
                        "attributes": {
                            "input": {
                                "project": "aws-lakehouse-platform",
                                "demo_id": "demo-20261026",
                                "expires_at": "2026-10-27T21:00:00Z",
                            }
                        }
                    }
                ],
            }
        ]
    }


def state_pull_format():
    state = approved_state()
    input_value = state["resources"][0]["instances"][0]["attributes"]["input"]
    state["resources"][0]["instances"][0]["attributes"]["input"] = {
        "value": input_value,
        "type": ["object", {"demo_id": "string", "expires_at": "string", "project": "string"}],
    }
    return state


class CleanupContextTests(unittest.TestCase):
    def test_derives_approved_context(self):
        context = derive_cleanup_context(approved_state())
        self.assertEqual(context.project, "aws-lakehouse-platform")
        self.assertEqual(context.demo_id, "demo-20261026")

    def test_derives_context_from_terraform_state_pull_format(self):
        context = derive_cleanup_context(state_pull_format())
        self.assertEqual(context.expires_at, "2026-10-27T21:00:00Z")

    def test_rejects_unexpected_project(self):
        state = copy.deepcopy(approved_state())
        state["resources"][0]["instances"][0]["attributes"]["input"]["project"] = "another-project"
        with self.assertRaises(CleanupContextRejected):
            derive_cleanup_context(state)

    def test_rejects_missing_context(self):
        with self.assertRaises(CleanupContextRejected):
            derive_cleanup_context({"resources": []})
