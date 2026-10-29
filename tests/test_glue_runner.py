import unittest

from lakehouse_platform.glue_runner import GlueInvocationRejected, SilverIndexInvocation, build_silver_index_arguments


def invocation(**overrides):
    values = {
        "batch_id": "initial-20261016",
        "landing_path": "s3://example/landing/batch_id=initial-20261016/consumption_index/",
        "silver_index_table": "glue_catalog.demo.consumption_index",
        "silver_interval_table": "glue_catalog.demo.meter_consumption_interval",
        "quarantine_table": "glue_catalog.demo.quarantine_records",
    }
    values.update(overrides)
    return SilverIndexInvocation(**values)


class GlueRunnerTests(unittest.TestCase):
    def test_builds_only_explicit_glue_job_arguments(self):
        arguments = build_silver_index_arguments(invocation())
        self.assertEqual("initial-20261016", arguments["BATCH_ID"])
        self.assertNotIn("AWS_ACCESS_KEY_ID", arguments)

    def test_rejects_non_landing_location(self):
        with self.assertRaises(GlueInvocationRejected):
            build_silver_index_arguments(invocation(landing_path="s3://example/warehouse/"))

    def test_rejects_non_iceberg_catalog_table(self):
        with self.assertRaises(GlueInvocationRejected):
            build_silver_index_arguments(invocation(quarantine_table="other.demo.quarantine_records"))
