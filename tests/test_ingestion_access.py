from copy import deepcopy
from unittest import TestCase
from unittest.mock import Mock

from lakehouse_platform.cleanup_guard import DestroyPlanRejected, validate_destroy_plan
from lakehouse_platform.ingestion_access import (
    INGESTION_ADDRESSES, assume_ingestion_session, expect_access_denied, validate_ingestion_identity,
)
from test_cleanup_guard import approved_plan, DEMO_ID, EXPIRES_AT

ACCOUNT = "000000000000"
BUCKET = "aws-lakehouse-platform-demo-test-aabbccdd"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/aws-lakehouse-platform-demo-test-ingestion"
IDENTITY = {"Account": ACCOUNT, "Arn": f"arn:aws:sts::{ACCOUNT}:assumed-role/aws-lakehouse-platform-demo-terraform-operator/test"}


class ServiceError(Exception):
    def __init__(self, code, status):
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}


class IngestionAccessTests(TestCase):
    def test_identity_requires_exact_demo_account_and_operator(self):
        validate_ingestion_identity(IDENTITY, ROLE, BUCKET)
        for identity, role, bucket in (
            (IDENTITY, ROLE.replace(ACCOUNT, "111111111111"), BUCKET),
            (IDENTITY, ROLE.replace("demo-test", "other-demo"), BUCKET),
            (IDENTITY, ROLE.replace("-ingestion", "-analyst"), BUCKET),
            (IDENTITY, ROLE, "other-bucket"),
            (IDENTITY, ROLE, BUCKET + "/landing"),
            ({**IDENTITY, "Arn": f"arn:aws:iam::{ACCOUNT}:user/admin"}, ROLE, BUCKET),
            ({**IDENTITY, "Arn": IDENTITY["Arn"].replace("terraform-operator/", "terraform-operator-evil/")}, ROLE, BUCKET),
        ):
            with self.subTest(role=role, bucket=bucket), self.assertRaises(ValueError):
                validate_ingestion_identity(identity, role, bucket)

    def test_assumption_uses_short_lived_credentials_only(self):
        operator = Mock(region_name="eu-west-3")
        operator.client.return_value.get_caller_identity.return_value = IDENTITY
        credentials = {"AccessKeyId": "temporary", "SecretAccessKey": "synthetic", "SessionToken": "session"}
        operator.client.return_value.assume_role.return_value = {"Credentials": credentials}
        factory = Mock()
        result = assume_ingestion_session(operator, ROLE, BUCKET, session_factory=factory)
        operator.client.return_value.assume_role.assert_called_once_with(RoleArn=ROLE, RoleSessionName="lakehouse-ingestion", DurationSeconds=900)
        self.assertIs(result, factory.return_value)
        self.assertEqual("session", factory.call_args.kwargs["aws_session_token"])
        self.assertNotIn("profile_name", factory.call_args.kwargs)

    def test_invalid_boundary_never_assumes_any_role(self):
        operator = Mock()
        operator.client.return_value.get_caller_identity.return_value = IDENTITY
        with self.assertRaises(ValueError):
            assume_ingestion_session(operator, ROLE, "foreign-bucket", session_factory=Mock())
        operator.client.return_value.assume_role.assert_not_called()

    def test_only_actual_authorization_failures_count_as_denial(self):
        self.assertTrue(expect_access_denied(Mock(side_effect=ServiceError("AccessDenied", 403)))["denied"])
        self.assertTrue(expect_access_denied(Mock(side_effect=ServiceError("AccessDeniedException", 400)), statuses=(400, 403))["denied"])
        for code, status in (("NoSuchKey", 404), ("ExpiredToken", 403), ("AccessDenied", 500), ("EntityNotFoundException", 400), ("AccessDeniedException", 400)):
            error = ServiceError(code, status)
            with self.subTest(code=code, status=status), self.assertRaises(ServiceError) as raised:
                expect_access_denied(Mock(side_effect=error))
            self.assertIs(error, raised.exception)
        with self.assertRaises(OSError):
            expect_access_denied(Mock(side_effect=OSError("network unavailable")))

    def test_successful_probe_is_rejected_and_response_stream_closed(self):
        body = Mock()
        with self.assertRaises(AssertionError):
            expect_access_denied(lambda: {"Body": body})
        body.close.assert_called_once()

    def test_cleanup_accepts_only_exact_ingestion_identity_and_policy(self):
        records = [r for r in approved_plan()["resource_changes"] if r["address"] in INGESTION_ADDRESSES]
        self.assertEqual(2, len(records))
        validate_destroy_plan({"resource_changes": records}, DEMO_ID, EXPIRES_AT)
        for record in records:
            for field, value in (("name", "unrelated"), ("name", "aws-lakehouse-platform-other-demo-ingestion")):
                changed = deepcopy(record)
                changed["change"]["before"][field] = value
                with self.assertRaises(DestroyPlanRejected):
                    validate_destroy_plan({"resource_changes": [changed]}, DEMO_ID, EXPIRES_AT)
        policy = next(r for r in records if r["address"].startswith("aws_iam_role_policy."))
        policy["change"]["before"]["role"] = "protected-operator"
        with self.assertRaises(DestroyPlanRejected):
            validate_destroy_plan({"resource_changes": [policy]}, DEMO_ID, EXPIRES_AT)

    def test_cleanup_requires_ingestion_role_tags_and_rejects_other_instances(self):
        role = next(r for r in approved_plan()["resource_changes"] if r["address"] == "aws_iam_role.ingestion[0]")
        role["change"]["before"]["tags_all"]["DemoId"] = "other-demo"
        with self.assertRaises(DestroyPlanRejected):
            validate_destroy_plan({"resource_changes": [role]}, DEMO_ID, EXPIRES_AT)
        role["address"] = "aws_iam_role.ingestion[1]"
        with self.assertRaises(DestroyPlanRejected):
            validate_destroy_plan({"resource_changes": [role]}, DEMO_ID, EXPIRES_AT)
