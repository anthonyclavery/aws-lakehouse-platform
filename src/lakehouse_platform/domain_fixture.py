from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import json
from pathlib import Path
import shutil
import time
from lakehouse_platform.generator import GeneratorConfig, build_reference_records, iter_consumption_indexes
from lakehouse_platform.intervals import ConsumptionIndex, derive_consumption_interval
from lakehouse_platform.pricing import iter_priced_segments

def write_domain_fixture(directory, config=None):
    config = config or GeneratorConfig()
    root = directory
    initial = root / 'initial'
    initial.mkdir(parents=True, exist_ok=False)
    refs = build_reference_records(config)
    files = []
    for name, rows in refs.items():
        path = initial / 'reference' / (name + '.jsonl')
        path.parent.mkdir(exist_ok=True)
        path.write_text(''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows))
        files.append(path)
    path = initial / 'consumption_index.jsonl'
    n = 0
    with path.open('w') as output:
        for row in iter_consumption_indexes(config, refs):
            output.write(json.dumps(row, sort_keys=True) + '\n')
            n += 1
    files.append(path)
    manifest = {'record_counts': {**{k: len(v) for k, v in refs.items()}, 'consumption_index': n}, 'files': [
        {'path': str(p.relative_to(initial)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]}
    (initial / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print('Full 100-customer / 200-site history generated:', n, 'indexes', flush=True)

    # Keep one meter in memory at a time. Hash every typed field in reading order.
    index_hashes, interval_hashes = {}, {}
    index_counts, interval_counts, energy = Counter(), Counter(), Counter()
    latest = {}
    totals = {name: defaultdict(lambda: {'consumption_kwh': Decimal(0), 'energy_cost_eur': Decimal(0)})
              for name in ('daily_site_energy', 'daily_customer_energy', 'monthly_customer_energy')}
    previous, current_meter = None, None
    interval_count = 0
    def intervals():
        nonlocal previous, current_meter, interval_count
        with path.open() as source:
            for line in source:
                row = json.loads(line)
                meter = row['meter_id']
                if meter != current_meter: previous, current_meter = None, meter
                fields = ('meter_id', 'reading_at', 'meter_index_wh', 'rollover_count', 'reading_context', 'source_version', 'received_at', 'schema_version')
                index_hashes.setdefault(meter, hashlib.sha256()).update(('|'.join(str(row[f]) for f in fields) + '\n').encode())
                index_counts[meter] += 1
                latest[meter] = row
                current = ConsumptionIndex(meter, datetime.fromisoformat(row['reading_at'].replace('Z', '+00:00')), row['meter_index_wh'], row['rollover_count'], row['reading_context'])
                interval = derive_consumption_interval(previous, current)
                if interval:
                    record = {'meter_id': meter, 'interval_start': interval.interval_start.isoformat().replace('+00:00', 'Z'),
                        'interval_end': row['reading_at'], 'consumption_wh': interval.consumption_wh, 'quality_status': interval.quality_status}
                    extended = {**record, 'previous_index_wh': previous.meter_index_wh, 'meter_index_wh': row['meter_index_wh'], 'rollover_count': row['rollover_count']}
                    fields = ('meter_id', 'interval_start', 'interval_end', 'previous_index_wh', 'meter_index_wh', 'rollover_count', 'consumption_wh', 'quality_status')
                    interval_hashes.setdefault(meter, hashlib.sha256()).update(('|'.join(str(extended[f]) for f in fields) + '\n').encode())
                    interval_counts[meter] += 1
                    energy[meter] += interval.consumption_wh
                    interval_count += 1
                    yield record
                previous = current

    started = time.monotonic()
    for nseg, segment in enumerate(iter_priced_segments(intervals(), refs), 1):
        segment['local_month'] = segment['local_date'][:7]
        for name, dimensions in (('daily_site_energy', ('customer_id', 'site_id', 'local_date')),
                                  ('daily_customer_energy', ('customer_id', 'local_date')), ('monthly_customer_energy', ('customer_id', 'local_month'))):
            target = totals[name][tuple(segment[key] for key in dimensions)]
            for field in ('consumption_kwh', 'energy_cost_eur'):
                target[field] += segment[field].quantize(Decimal('1e-12'), rounding=ROUND_HALF_EVEN)
        if nseg % 100000 == 0: print('Priced', nseg, 'segments in', round(time.monotonic() - started), 'seconds', flush=True)
    products = {}
    for name, dimensions in (('daily_site_energy', ('customer_id', 'site_id', 'local_date')),
                              ('daily_customer_energy', ('customer_id', 'local_date')), ('monthly_customer_energy', ('customer_id', 'local_month'))):
        products[name] = [{**dict(zip(dimensions, key)), **value} for key, value in sorted(totals[name].items())]
    proof = {'customers': len({r['customer_id'] for r in refs['customer_site']}), 'sites': len(refs['site_meter']), 'contracts': dict(Counter(row['contract_type'] for row in refs['site_contract'])),
        'index_count': sum(index_counts.values()), 'interval_count': interval_count, 'raw_energy_wh': sum(energy.values()),
        'indexes_by_meter': {k: {'n': index_counts[k], 'sha256': v.hexdigest()} for k, v in index_hashes.items()},
        'intervals_by_meter': {k: {'n': interval_counts[k], 'sha256': v.hexdigest()} for k, v in interval_hashes.items()}}
    (root / 'expected-products.json').write_text(json.dumps(products, default=str))
    (root / 'expected-domain.json').write_text(json.dumps(proof, indent=2))
    (root / 'latest.json').write_text(json.dumps(latest))
    print('Streaming full-domain oracle complete:', proof['index_count'], proof['interval_count'], proof['raw_energy_wh'], flush=True)

    return proof
