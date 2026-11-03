"""Small source-shaped batches crossing weekend and tariff-hour boundaries."""
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path

from .generator import stable_uuid

CONTRACTS = ("BASE", "WEEKEND", "PEAK_OFF_PEAK")


def write_integrated_fixture(directory: Path, *, include_append=True):
    directory.mkdir(parents=True, exist_ok=False)
    metadata = {"source_version": 1, "received_at": "2026-11-03T00:00:00Z", "schema_version": 1}
    valid = {**metadata, "valid_from": "2020-01-01T00:00:00Z", "valid_to": None}
    refs = {name: [] for name in ("site_meter", "customer_site", "site_contract", "tariff_grid")}
    start = datetime(2026, 1, 9, 20, tzinfo=UTC)  # Friday 21:00 in Paris.
    initial, append = [], []
    for index, contract in enumerate(CONTRACTS):
        meter, site, customer = [stable_uuid("integrated-" + kind, index) for kind in ("meter", "site", "customer")]
        refs["site_meter"].append({**valid, "site_id": site, "meter_id": meter})
        refs["customer_site"].append({**valid, "site_id": site, "customer_id": customer})
        refs["site_contract"].append({**valid, "site_id": site, "contract_id": stable_uuid("integrated-contract", index), "contract_type": contract})
        for hour in (0, 2, 10, 11):
            row = {**metadata, "meter_id": meter, "reading_at": (start + timedelta(hours=hour)).isoformat().replace("+00:00", "Z"),
                   "meter_index_wh": 1000 + hour * 1000, "rollover_count": 0, "reading_context": "NORMAL"}
            (append if hour == 11 else initial).append(row)
    rules = (("BASE", "ALL_DAYS", None, None, "0.20"),
             ("WEEKEND", "WEEKDAY", None, None, "0.20"), ("WEEKEND", "WEEKEND", None, None, "0.10"),
             ("PEAK_OFF_PEAK", "ALL_DAYS", "06:00", "22:00", "0.30"),
             ("PEAK_OFF_PEAK", "ALL_DAYS", "22:00", "06:00", "0.10"))
    for index, (contract, day, begin, end, price) in enumerate(rules):
        refs["tariff_grid"].append({**valid, "tariff_rule_id": stable_uuid("integrated-tariff", index), "contract_type": contract,
                                   "day_scope": day, "time_from_local": begin, "time_to_local": end, "price_eur_per_kwh": price})
    batches = [("initial", initial)]
    if include_append:
        batches.append(("append", append))
    for name, indexes in batches:
        batch = directory / name
        batch.mkdir()
        files = {"consumption_index.jsonl": indexes}
        if name == "initial":
            files.update({f"reference/{flow}.jsonl": rows for flow, rows in refs.items()})
        manifest = {"files": [], "record_counts": {}}
        for relative, rows in files.items():
            path = batch / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            data = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode()
            path.write_bytes(data)
            manifest["files"].append({"path": relative, "sha256": hashlib.sha256(data).hexdigest()})
            manifest["record_counts"][relative] = len(rows)
        (batch / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return {"contracts": list(CONTRACTS), "initial_indexes": 9, "append_indexes": 3,
            "initial_intervals": 6, "final_intervals": 9,
            "initial_total_kwh": "30", "initial_total_eur": "4.70",
            "final_total_kwh": "33", "final_total_eur": "5.30"}
