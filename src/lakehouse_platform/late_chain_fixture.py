"""Late indexes and higher-version corrections for the governed identity chain."""
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path

from .generator import stable_uuid
from .integrated_fixture import write_integrated_fixture


def write_late_chain_fixture(directory: Path):
    summary = write_integrated_fixture(directory, include_append=False)
    start = datetime(2026, 1, 9, 20, tzinfo=UTC)
    for name, hour, value, version, received in (
        ("late", 3, 5000, 1, "2026-11-03T01:00:00Z"),
        ("correction", 2, 4000, 2, "2026-11-03T02:00:00Z"),
    ):
        batch = directory / name
        batch.mkdir()
        rows = [{"meter_id": stable_uuid("integrated-meter", meter),
                 "reading_at": (start + timedelta(hours=hour)).isoformat().replace("+00:00", "Z"),
                 "meter_index_wh": value, "rollover_count": 0, "reading_context": "NORMAL", "source_version": version,
                 "received_at": received, "schema_version": 1} for meter in range(3)]
        path = batch / "consumption_index.jsonl"
        data = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
        path.write_bytes(data)
        manifest = {"files": [{"path": path.name, "sha256": hashlib.sha256(data).hexdigest()}], "record_counts": {"consumption_index": 3}}
        (batch / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return {"contracts": summary["contracts"], "phases": {
        "initial": {"indexes": 9, "intervals": 6, "raw_consumption_wh": 30000, "rounded_gold_kwh": "30.000000000000", "rounded_gold_eur": "4.700000000000", "exact_cost_fraction": "47/10"},
        "late": {"indexes": 12, "intervals": 9, "raw_consumption_wh": 30000, "rounded_gold_kwh": "30.000000000003", "rounded_gold_eur": "4.771428571428", "exact_cost_fraction": "167/35"},
        "correction": {"indexes": 12, "intervals": 9, "raw_consumption_wh": 30000, "rounded_gold_kwh": "30.000000000003", "rounded_gold_eur": "4.871428571428", "exact_cost_fraction": "341/70"},
    }, "corrected_keys": 3, "corrected_version": 2}
