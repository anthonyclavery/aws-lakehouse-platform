from unittest import TestCase
from unittest.mock import Mock, patch
from lakehouse_platform.demo_aws import wait_for_ingestion


class Denied(Exception):
    response = {"Error": {"Code": "AccessDenied"}}


class ReadinessTests(TestCase):
    def test_only_credential_denial_is_retried_and_identity_is_checked(self):
        role = "arn:aws:iam::000000000000:role/demo-ingestion"
        session, pause = Mock(), Mock()
        session.client.return_value.get_caller_identity.return_value = {"Arn": "arn:aws:sts::000000000000:assumed-role/demo-ingestion/test"}
        with patch("lakehouse_platform.demo_aws.assume_ingestion_session", side_effect=[Denied(), session]) as assume:
            wait_for_ingestion(Mock(), role, "bucket", pause=pause)
        self.assertEqual(2, assume.call_count)
        pause.assert_called_once_with(5)
        with patch("lakehouse_platform.demo_aws.assume_ingestion_session", side_effect=Denied()) as assume:
            with self.assertRaises(Denied): wait_for_ingestion(Mock(), role, "bucket", attempts=2, pause=Mock())
        self.assertEqual(2, assume.call_count)
        with patch("lakehouse_platform.demo_aws.assume_ingestion_session", side_effect=ValueError("wrong scope")) as assume:
            with self.assertRaises(ValueError): wait_for_ingestion(Mock(), role, "bucket", pause=Mock())
        assume.assert_called_once()
        session.client.return_value.get_caller_identity.return_value = {"Arn": "wrong-role"}
        with patch("lakehouse_platform.demo_aws.assume_ingestion_session", return_value=session):
            with self.assertRaises(ValueError): wait_for_ingestion(Mock(), role, "bucket", pause=Mock())
