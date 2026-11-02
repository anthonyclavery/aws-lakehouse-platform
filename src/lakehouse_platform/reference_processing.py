"""Local Silver oracle for versioned temporal reference flows."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


class ReferenceDataRejected(ValueError):
    """Raised when versioned reference data violates a V1 temporal rule."""


BUSINESS_KEYS = {
    "site_meter": ("site_id", "valid_from"),
    "customer_site": ("site_id", "valid_from"),
    "site_contract": ("contract_id",),
    "tariff_grid": ("tariff_rule_id",),
}


def process_reference_data(batch_dirs: list[Path]) -> dict[str, list[dict[str, object]]]:
    latest: dict[str, dict[tuple[object, ...], dict[str, object]]] = {flow: {} for flow in BUSINESS_KEYS}
    for directory in batch_dirs:
        for path in sorted((directory / "reference").glob("*.jsonl")) if (directory / "reference").exists() else []:
            flow = path.stem
            if flow not in BUSINESS_KEYS:
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                row = json.loads(line)
                key = tuple(row[field] for field in BUSINESS_KEYS[flow])
                previous = latest[flow].get(key)
                if previous is None or (int(row["source_version"]), row["received_at"]) > (int(previous["source_version"]), previous["received_at"]):
                    latest[flow][key] = row

    result = {flow: sorted(rows.values(), key=lambda row: tuple(str(row[field]) for field in BUSINESS_KEYS[flow])) for flow, rows in latest.items()}
    _validate_single_active_relationship(result["site_meter"], "site_id", "site_meter")
    _validate_single_active_relationship(result["customer_site"], "site_id", "customer_site")
    return result


def _validate_single_active_relationship(rows: list[dict[str, object]], group_field: str, flow: str) -> None:
    active = defaultdict(int)
    for row in rows:
        if row.get("valid_to") is None:
            active[str(row[group_field])] += 1
    duplicates = sorted(key for key, count in active.items() if count > 1)
    if duplicates:
        raise ReferenceDataRejected(f"{flow} has more than one active relationship for {duplicates[0]!r}")
