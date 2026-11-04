import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from lakehouse_platform.domain_validation import FULL_INTERVAL_FIELDS, compare_hashes, hash_query, qualify_quarantine, write_invalid_fixture
from lakehouse_platform.integrated_validation import INDEX_FIELDS
from lakehouse_platform.landing import plan_landing_upload


class DomainAcceptanceTests(TestCase):
    def test_full_typed_row_hashes_detect_mutations_counts_and_duplicates(self):
        expected = {'meter': {'n': 3, 'sha256': hashlib.sha256(b'all eight typed fields').hexdigest()}}
        actual = [{'meter_id': 'meter', **expected['meter']}]
        self.assertTrue(compare_hashes(expected, actual)['passed'])
        self.assertFalse(compare_hashes(expected, actual * 2)['passed'])
        self.assertFalse(compare_hashes(expected, [{**actual[0], 'n': 4}])['passed'])
        self.assertFalse(compare_hashes(expected, [{**actual[0], 'sha256': '0' * 64}])['passed'])
        for table, fields, order in (('consumption_index', INDEX_FIELDS, 'reading_at'),
                                     ('meter_consumption_interval', FULL_INTERVAL_FIELDS, 'interval_end')):
            sql = hash_query('aws_lakehouse_platform_example', table, fields, order)
            self.assertIn('SHA256', sql)
            self.assertIn('ORDER BY ' + order, sql)
            self.assertTrue(all(field in sql for field in fields))
        with self.assertRaises(ValueError): hash_query('aws_lakehouse_platform_example', 'other', INDEX_FIELDS, 'reading_at')

    def test_invalid_fixture_is_manifest_backed_and_audits_exact_typed_quarantine(self):
        valid = {'meter_id': 'existing', 'reading_at': '2026-10-16T00:00:00Z', 'received_at': '2026-10-16T00:05:00Z',
                 'meter_index_wh': 1000, 'rollover_count': 0, 'source_version': 1, 'schema_version': 1, 'reading_context': 'NORMAL'}
        with TemporaryDirectory() as folder:
            root = Path(folder) / 'invalid'
            expected = write_invalid_fixture(root, valid)
            self.assertEqual(10, len(expected))
            self.assertEqual(2, len(plan_landing_upload(root, 'invalid-case')))
            self.assertEqual('existing', expected[0]['meter_id'])
            self.assertEqual(valid['reading_at'], expected[0]['reading_at'])
            self.assertEqual(99, expected[0]['source_version'])
            self.assertEqual(1, valid['source_version'])
            self.assertEqual(1, sum(row['reading_at'] is None for row in expected))
            self.assertEqual(1, sum(row['meter_index_wh'] is None for row in expected))
            stats = {'ResultReuseInformation': {'ReusedPreviousResult': False}}
            with patch('lakehouse_platform.domain_validation.query_rows', return_value=(expected, stats)):
                self.assertTrue(qualify_quarantine(Mock(), 'aws_lakehouse_platform_example', 'workgroup', expected)['passed'])
            with patch('lakehouse_platform.domain_validation.query_rows', return_value=(expected[:-1], stats)):
                self.assertFalse(qualify_quarantine(Mock(), 'aws_lakehouse_platform_example', 'workgroup', expected)['passed'])
            with patch('lakehouse_platform.domain_validation.query_rows', return_value=(expected, {})):
                with self.assertRaises(ValueError): qualify_quarantine(Mock(), 'aws_lakehouse_platform_example', 'workgroup', expected)
