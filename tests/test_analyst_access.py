from copy import deepcopy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from analyst_fixtures import analyst_before
from lakehouse_platform.analyst_access import (
    GOLD, access_addresses, authorization_failure, expect_catalog_denied, expect_object_denied,
    expect_query_denied, gold_location, is_legacy, seal_gold, table_resource, temporary_administrator, validate_delete,
)


class AnalystAccessTests(unittest.TestCase):
    def settings_client(self):
        original = {"DataLakeAdmins": [], "CreateDatabaseDefaultPermissions": [{"Permissions": ["ALL"]}],
                    "CreateTableDefaultPermissions": [{"Permissions": ["ALL"]}], "Parameters": {"CROSS_ACCOUNT_VERSION": "4"}}
        current = deepcopy(original)
        client = Mock()
        client.get_data_lake_settings.side_effect = lambda: {"DataLakeSettings": deepcopy(current)}
        def put(**kwargs):
            current.clear()
            current.update(deepcopy(kwargs["DataLakeSettings"]))
        client.put_data_lake_settings.side_effect = put
        return client, original, current

    def test_temporary_bootstrap_restores_defaults_and_admins(self):
        client, original, current = self.settings_client()
        with temporary_administrator(client, "arn:aws:iam::000000000000:user/governance") as record:
            self.assertEqual(original["CreateTableDefaultPermissions"], current["CreateTableDefaultPermissions"])
            self.assertEqual(1, len(current["DataLakeAdmins"]))
        self.assertEqual(original, current)
        self.assertTrue(record["restored"])

    def test_failed_apply_also_removes_temporary_administrator(self):
        client, original, current = self.settings_client()
        with self.assertRaisesRegex(ValueError, "apply failed"):
            with temporary_administrator(client, "arn:aws:iam::000000000000:user/governance"):
                raise ValueError("apply failed")
        self.assertEqual(original, current)

    def test_preexisting_admin_is_never_removed(self):
        client, original, current = self.settings_client()
        principal = "arn:aws:iam::000000000000:user/governance"
        current["DataLakeAdmins"] = [{"DataLakePrincipalIdentifier": principal}]
        with temporary_administrator(client, principal) as record:
            self.assertFalse(record["temporarily_added"])
        client.put_data_lake_settings.assert_not_called()
        self.assertEqual(principal, current["DataLakeAdmins"][0]["DataLakePrincipalIdentifier"])

    def test_concurrent_change_is_preserved_and_reported_not_silently_overwritten(self):
        client, _, current = self.settings_client()
        with self.assertRaisesRegex(RuntimeError, "concurrently"):
            with temporary_administrator(client, "arn:aws:iam::000000000000:user/governance"):
                current["DataLakeAdmins"].append({"DataLakePrincipalIdentifier": "arn:aws:iam::000000000000:role/concurrent"})
                current["Parameters"]["other"] = "retained"
        self.assertEqual([{"DataLakePrincipalIdentifier": "arn:aws:iam::000000000000:role/concurrent"}], current["DataLakeAdmins"])
        self.assertEqual("retained", current["Parameters"]["other"])

    def test_sts_session_arn_is_not_accepted_as_a_canonical_admin(self):
        with self.assertRaises(ValueError):
            with temporary_administrator(Mock(), "arn:aws:sts::000000000000:assumed-role/admin/session"):
                pass

    def test_gold_scope_and_location_reject_silver_other_buckets_and_sql(self):
        database = "aws_lakehouse_platform_demo"
        for table in ("consumption_index", "*", "daily_site_energy;DROP"):
            with self.assertRaises(ValueError):
                table_resource("000000000000", database, table)
        location = "s3://project/warehouse/aws_lakehouse_platform_demo.db/daily_site_energy/"
        self.assertEqual(location.rstrip("/"), gold_location("project", database, GOLD[0], location))
        for invalid in (location.replace("project/", "other/"), location.replace("daily_site_energy", "consumption_index"),
                        location + "../secret", location + "?token=bad"):
            with self.assertRaises(ValueError):
                gold_location("project", database, GOLD[0], invalid)

    def test_sealing_requires_explicit_grants_before_revoking_legacy_super(self):
        client = Mock()
        client.list_permissions.return_value = {"PrincipalResourcePermissions": [{"Principal": {"DataLakePrincipalIdentifier": "IAM_ALLOWED_PRINCIPALS"}, "Permissions": ["ALL"]}]}
        with self.assertRaisesRegex(ValueError, "missing"):
            seal_gold(client, "000000000000", "aws_lakehouse_platform_demo", "analyst", "writer", "governance", "cleanup")
        client.revoke_permissions.assert_not_called()

    def test_only_legacy_grants_on_exact_gold_tables_are_revoked(self):
        client = Mock()
        removed = set()
        def listed(**kwargs):
            name = kwargs["Resource"]["Table"]["Name"]
            rows = [{"Principal": {"DataLakePrincipalIdentifier": actor}, "Permissions": perms,
                     "PermissionsWithGrantOption": ["ALL"] if actor in ("governance", "cleanup") else []}
                    for actor, perms in (("analyst", ["SELECT", "DESCRIBE"]), ("writer", ["ALL"]), ("governance", ["ALL"]), ("cleanup", ["ALL"]))]
            if name not in removed:
                rows.append({"Principal": {"DataLakePrincipalIdentifier": "IAM_ALLOWED_PRINCIPALS"}, "Permissions": ["ALL"]})
            return {"PrincipalResourcePermissions": rows}
        client.list_permissions.side_effect = listed
        client.revoke_permissions.side_effect = lambda **kwargs: removed.add(kwargs["Resource"]["Table"]["Name"])
        seal_gold(client, "000000000000", "aws_lakehouse_platform_demo", "analyst", "writer", "governance", "cleanup")
        self.assertEqual(set(GOLD), removed)
        self.assertEqual(3, client.revoke_permissions.call_count)
        self.assertTrue(is_legacy("IAM_Allowed_Principals"))

    def test_every_analyst_destroy_pointer_is_bound_to_the_demo(self):
        for address in access_addresses():
            with self.subTest(address=address):
                validate_delete(address, analyst_before(address, "demo-20261103"), "demo-20261103")
                with self.assertRaises(ValueError):
                    validate_delete(address, analyst_before(address, "another-demo"), "demo-20261103")

    def test_cleanup_rejects_wildcards_cross_catalog_and_broader_permissions(self):
        address = 'aws_lakeformation_permissions.analyst_gold["daily_site_energy"]'
        before = analyst_before(address, "demo-20261103")
        for field, value in (("wildcard", True), ("catalog_id", "999999999999"), ("name", "consumption_index")):
            data = deepcopy(before)
            data["table"][0][field] = value
            with self.assertRaises(ValueError):
                validate_delete(address, data, "demo-20261103")
        before["permissions"].append("ALTER")
        with self.assertRaises(ValueError):
            validate_delete(address, before, "demo-20261103")

    def test_authorization_messages_do_not_accept_generic_failures(self):
        for message in ("AccessDeniedException", "Insufficient permissions to execute the query.", "Insufficient Lake Formation permission(s): Required SELECT"):
            self.assertTrue(authorization_failure(message))
        for message in ("TABLE_NOT_FOUND", "SYNTAX_ERROR", "connection closed", "ExpiredToken"):
            self.assertFalse(authorization_failure(message))

    def test_masked_not_found_requires_independent_catalog_denial(self):
        with patch("lakehouse_platform.qualification.query_rows", side_effect=RuntimeError("TABLE_NOT_FOUND")):
            with self.assertRaises(RuntimeError):
                expect_query_denied(Mock(), "db", "wg", "SELECT 1")
            result = expect_query_denied(Mock(), "db", "wg", "SELECT 1", confirmed_catalog_denial=True)
        self.assertTrue(result["catalog_masked_not_found"])

    def test_successful_unauthorized_query_is_never_counted_as_a_denial(self):
        with patch("lakehouse_platform.qualification.query_rows", return_value=([], {})):
            with self.assertRaises(AssertionError):
                expect_query_denied(Mock(), "db", "wg", "SELECT 1")

    def test_missing_or_expired_s3_objects_are_not_counted_as_denials(self):
        class Error(Exception):
            def __init__(self, code, status):
                self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}
        modules = {"botocore": SimpleNamespace(), "botocore.exceptions": SimpleNamespace(ClientError=Error)}
        client = Mock()
        with patch.dict("sys.modules", modules):
            client.get_object.side_effect = Error("AccessDenied", 403)
            self.assertTrue(expect_object_denied(client, "bucket", "key")["denied"])
            for code, status in (("NoSuchKey", 404), ("ExpiredToken", 403), ("AccessDenied", 400)):
                client.get_object.side_effect = Error(code, status)
                with self.subTest(code=code, status=status), self.assertRaises(Error):
                    expect_object_denied(client, "bucket", "key")
            client.get_object.side_effect = None
            body = Mock()
            client.get_object.return_value = {"Body": body}
            with self.assertRaises(AssertionError):
                expect_object_denied(client, "bucket", "key")
            body.close.assert_called_once()

    def test_catalog_not_found_does_not_substitute_for_an_iam_denial(self):
        class Error(Exception):
            def __init__(self, code, message):
                self.response = {"Error": {"Code": code, "Message": message}}
        modules = {"botocore": SimpleNamespace(), "botocore.exceptions": SimpleNamespace(ClientError=Error)}
        client = Mock()
        with patch.dict("sys.modules", modules):
            client.get_table.side_effect = Error("EntityNotFoundException", "Table missing")
            with self.assertRaises(Error):
                expect_catalog_denied(client, "db", "table")
            client.get_table.side_effect = Error("AccessDeniedException", "User is not authorized to perform glue:GetTable")
            self.assertTrue(expect_catalog_denied(client, "db", "table")["denied"])
