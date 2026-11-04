import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from lakehouse_platform.domain_validation import FULL_INTERVAL_FIELDS, compare_hashes, hash_query, qualify_domain, qualify_quarantine, write_invalid_fixture
from lakehouse_platform.integrated_validation import INDEX_FIELDS
from lakehouse_platform.landing import plan_landing_upload
from lakehouse_platform.domain_fixture import write_domain_fixture
from lakehouse_platform.integrated_fixture import write_integrated_fixture
from lakehouse_platform.reference_processing import process_reference_data
from lakehouse_platform.qualification import expected_products


class DomainAcceptanceTests(TestCase):
    def test_operational_and_gold_reads_use_their_distinct_credentials(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            write_integrated_fixture(root / 'compact')
            batch = root / 'compact/initial'
            refs = process_reference_data([batch])
            products = json.loads(json.dumps(expected_products([batch]), default=str))
            meter_hashes = {f'meter-{index}': {'n': 1, 'sha256': 'a' * 64} for index in range(200)}
            expected = {'customers': 100, 'sites': 200, 'contracts': {'BASE': 100, 'WEEKEND': 80, 'PEAK_OFF_PEAK': 20},
                'indexes_by_meter': meter_hashes, 'intervals_by_meter': meter_hashes, 'index_count': 200, 'interval_count': 200, 'raw_energy_wh': 30000}
            (root / 'expected-domain.json').write_text(json.dumps(expected))
            (root / 'expected-products.json').write_text(json.dumps(products))
            operator, analyst = Mock(), Mock()
            def query(client, database, group, sql):
                for table, rows in products.items():
                    if f'"{table}"' in sql:
                        self.assertIs(analyst, client)
                        self.assertEqual('analyst-workgroup', group)
                        return rows, {'ResultReuseInformation': {'ReusedPreviousResult': False}}
                self.assertIs(operator, client)
                self.assertEqual('operator-workgroup', group)
                for flow, rows in refs.items():
                    if f'"{flow}_history"' in sql: return rows, {'ResultReuseInformation': {'ReusedPreviousResult': False}}
                return [{'meter_id': key, **row} for key, row in meter_hashes.items()], {'ResultReuseInformation': {'ReusedPreviousResult': False}}
            with patch('lakehouse_platform.domain_validation.process_reference_data', return_value=refs), \
                 patch('lakehouse_platform.domain_validation.query_rows', side_effect=query):
                result = qualify_domain(operator, 'aws_lakehouse_platform_example', 'operator-workgroup', root,
                    analyst_athena=analyst, analyst_workgroup='analyst-workgroup')
            self.assertTrue(result['passed'])
            self.assertEqual('dedicated analyst', result['reader_boundaries']['gold'])

    def test_streaming_oracle_matches_existing_complete_business_calculator(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            write_integrated_fixture(root / 'compact')
            batch = root / 'compact/initial'
            references = process_reference_data([batch])
            rows = sorted([json.loads(line) for line in (batch / 'consumption_index.jsonl').read_text().splitlines()],
                          key=lambda row: (row['meter_id'], row['reading_at']))
            with patch('lakehouse_platform.domain_fixture.build_reference_records', return_value=references), \
                 patch('lakehouse_platform.domain_fixture.iter_consumption_indexes', return_value=iter(rows)):
                proof = write_domain_fixture(root / 'streamed')
            actual = json.loads((root / 'streamed/expected-products.json').read_text())
            self.assertEqual(json.loads(json.dumps(expected_products([batch]), default=str)), actual)
            self.assertEqual((9, 6, 30000), (proof['index_count'], proof['interval_count'], proof['raw_energy_wh']))
            self.assertEqual(6, len(plan_landing_upload(root / 'streamed/initial', 'streamed-test')))

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
