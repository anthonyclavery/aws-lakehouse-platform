"""Full historical acceptance using ordered per-meter SHA-256 and complete Gold rows."""
import hashlib
import json
from pathlib import Path

from .analyst_access import validate_database
from .integrated_validation import INDEX_FIELDS, INTERVAL_FIELDS, REFERENCE_FIELDS, compare_silver_rows
from .qualification import compare_product, query_rows
from .reference_processing import process_reference_data

FULL_INTERVAL_FIELDS = ('meter_id', 'interval_start', 'interval_end', 'previous_index_wh', 'meter_index_wh',
                        'rollover_count', 'consumption_wh', 'quality_status')


def hash_query(database, table, fields, order):
    validate_database(database)
    if (table, tuple(fields), order) not in (("consumption_index", INDEX_FIELDS, "reading_at"),
            ("meter_consumption_interval", FULL_INTERVAL_FIELDS, "interval_end")):
        raise ValueError("Only the two complete accepted data schemas may be hashed")
    timestamps = {"reading_at", "received_at", "interval_start", "interval_end"}
    values = [f"date_format({field}, '%Y-%m-%dT%H:%i:%sZ')" if field in timestamps else f"CAST({field} AS varchar)" for field in fields]
    row = "CONCAT_WS('|', " + ", ".join(values) + ")"
    return f'''SELECT meter_id, CAST(COUNT(*) AS varchar) AS n,
      LOWER(TO_HEX(SHA256(TO_UTF8(CONCAT(ARRAY_JOIN(ARRAY_AGG({row} ORDER BY {order}), CHR(10)), CHR(10)))))) AS sha256
      FROM "{database}"."{table}" GROUP BY meter_id'''


def compare_hashes(expected, actual):
    indexed = {row['meter_id']: {'n': int(row['n']), 'sha256': row['sha256']} for row in actual}
    return {'passed': len(indexed) == len(actual) and indexed == expected,
            'meters': len(indexed), 'rows': sum(row['n'] for row in indexed.values()),
            'expected_digest': hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest(),
            'actual_digest': hashlib.sha256(json.dumps(indexed, sort_keys=True).encode()).hexdigest()}


def qualify_domain(athena, database, workgroup, directory, *, analyst_athena, analyst_workgroup):
    directory = Path(directory)
    expected = json.loads((directory / 'expected-domain.json').read_text())
    products = json.loads((directory / 'expected-products.json').read_text())
    if (expected['customers'], expected['sites'], expected['contracts']) != (100, 200, {'BASE': 100, 'WEEKEND': 80, 'PEAK_OFF_PEAK': 20}):
        raise ValueError("Full V1 acceptance requires the documented 100-customer/200-site topology")
    report = {'accepted': {}, 'references': {}, 'gold': {}, 'query_statistics': []}
    def query(sql, *, client=athena, group=workgroup):
        rows, stats = query_rows(client, database, group, sql)
        if stats.get('ResultReuseInformation', {}).get('ReusedPreviousResult') is not False:
            raise ValueError("Full-domain acceptance requires fresh queries")
        report['query_statistics'].append(stats)
        return rows
    for table, fields, order, key in (('consumption_index', INDEX_FIELDS, 'reading_at', 'indexes_by_meter'),
            ('meter_consumption_interval', FULL_INTERVAL_FIELDS, 'interval_end', 'intervals_by_meter')):
        report['accepted'][table] = compare_hashes(expected[key], query(hash_query(database, table, fields, order)))
    references = process_reference_data([directory / 'initial'])
    for flow, fields in REFERENCE_FIELDS.items():
        fields = (*fields, 'valid_from', 'valid_to', 'source_version', 'received_at', 'schema_version')
        columns = ', '.join(f'CAST("{field}" AS varchar) AS "{field}"' for field in fields)
        actual = query(f'SELECT {columns} FROM "{database}"."{flow}_history"')
        report['references'][flow] = compare_silver_rows(references[flow], actual, fields)
    for table, rows in products.items():
        columns = ', '.join(f'CAST("{field}" AS varchar) AS "{field}"' for field in rows[0])
        report['gold'][table] = compare_product(table, rows, query(f'SELECT {columns} FROM "{database}"."{table}"',
            client=analyst_athena, group=analyst_workgroup))
    report['reader_boundaries'] = {'indexes_intervals_references': 'project operator', 'gold': 'dedicated analyst'}
    report['passed'] = all(row['passed'] for section in ('accepted', 'references', 'gold') for row in report[section].values())
    report['profile'] = {key: expected[key] for key in ('customers', 'sites', 'contracts', 'index_count', 'interval_count', 'raw_energy_wh')}
    return report


def write_invalid_fixture(directory, valid_reading):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    rows, expected = [], []
    changes = [('negative_register', {'meter_index_wh': -1}), ('oversized_register', {'meter_index_wh': 999999001}),
        ('invalid_timestamp', {'reading_at': 'invalid'}), ('invalid_context', {'reading_context': 'UNKNOWN'}),
        ('unsupported_schema', {'schema_version': 2}), ('invalid_version', {'source_version': 0}),
        ('negative_rollover', {'rollover_count': -1}), ('null_meter', {'meter_id': None}),
        ('invalid_received_at', {'received_at': 'invalid'}), ('invalid_number', {'meter_index_wh': 'invalid'})]
    for index, (name, change) in enumerate(changes):
        row = {**valid_reading, 'meter_id': 'invalid-' + name, 'reading_at': f'2026-10-17T{index:02}:00:00Z',
               'source_version': 99, **change}
        if index == 0:
            # A rejected higher version must not replace an existing accepted key.
            row.update(meter_id=valid_reading['meter_id'], reading_at=valid_reading['reading_at'])
        rows.append(row)
        typed = {**row, 'quarantine_reason': 'CONTRACT_OR_VALUE_VIOLATION'}
        for field in ('reading_at', 'received_at', 'meter_index_wh'):
            if typed[field] == 'invalid': typed[field] = None
        expected.append(typed)
    data = ''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows).encode()
    (directory / 'consumption_index.jsonl').write_bytes(data)
    (directory / 'manifest.json').write_text(json.dumps({'record_counts': {'consumption_index': len(rows)},
        'files': [{'path': 'consumption_index.jsonl', 'sha256': hashlib.sha256(data).hexdigest()}]}, indent=2))
    return expected


def qualify_quarantine(athena, database, workgroup, expected):
    fields = (*INDEX_FIELDS, 'quarantine_reason')
    columns = ', '.join(f'CAST("{field}" AS varchar) AS "{field}"' for field in fields)
    actual, stats = query_rows(athena, database, workgroup, f'SELECT {columns} FROM "{database}"."quarantine_records"')
    if stats.get('ResultReuseInformation', {}).get('ReusedPreviousResult') is not False:
        raise ValueError("Quarantine audit requires a fresh query")
    return compare_silver_rows(expected, actual, fields)
