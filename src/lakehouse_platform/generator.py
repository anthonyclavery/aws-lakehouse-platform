"""Deterministic, public-safe synthetic smart-meter batch generator."""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


NAMESPACE = uuid.UUID("5d63ed8e-3864-4a76-b8ce-c4e9d633b842")
REGISTER_MAX_WH = 999_999_000
SCHEMA_VERSION = 1
PARIS = ZoneInfo("Europe/Paris")
CONTRACT_TYPES = ("BASE", "WEEKEND", "PEAK_OFF_PEAK")


@dataclass(frozen=True)
class GeneratorConfig:
    customers: int = 100
    sites: int = 200
    seed: int = 20261016
    as_of: datetime = datetime(2026, 10, 16, tzinfo=UTC)
    output_dir: Path = Path("artifacts/initial")
    include_indexes: bool = True


def stable_uuid(kind: str, position: int) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{kind}:{position}"))


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def local_midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, PARIS).astimezone(UTC)


def allocate_contract_types(site_ids: list[str], rng: random.Random) -> dict[str, str]:
    """Allocate the documented 50/40/10 distribution without site ordering bias."""
    count = len(site_ids)
    base = round(count * 0.50)
    weekend = round(count * 0.40)
    peak_off_peak = count - base - weekend
    shuffled = site_ids[:]
    rng.shuffle(shuffled)
    return {
        site_id: "BASE" if index < base else "WEEKEND" if index < base + weekend else "PEAK_OFF_PEAK"
        for index, site_id in enumerate(shuffled)
    }


def build_reference_records(config: GeneratorConfig) -> dict[str, list[dict[str, object]]]:
    """Create temporal reference flows, with synthetic UUIDs and no personal data."""
    if config.customers < 1:
        raise ValueError("customers must be positive")
    if config.sites < config.customers:
        raise ValueError("sites must be greater than or equal to customers")

    rng = random.Random(config.seed)
    as_of_local_day = config.as_of.astimezone(PARIS).date()
    customer_ids = [stable_uuid("customer", item) for item in range(config.customers)]
    site_ids = [stable_uuid("site", item) for item in range(config.sites)]
    contract_types = allocate_contract_types(site_ids, rng)
    received_at = config.as_of

    multi_count = min(config.customers, max(1, round(config.customers * 0.20)))
    site_owners: list[str] = customer_ids[:]
    for index in range(config.sites - config.customers):
        site_owners.append(customer_ids[index % multi_count])

    recent_count = max(1, round(config.sites * 0.02))
    recent_sites = set(rng.sample(site_ids, recent_count))
    records: dict[str, list[dict[str, object]]] = {
        "customer_site": [],
        "site_meter": [],
        "site_contract": [],
        "tariff_grid": _tariff_grid(received_at),
    }
    for index, site_id in enumerate(site_ids):
        meter_id = stable_uuid("meter", index)
        if site_id in recent_sites:
            start_day = as_of_local_day - timedelta(days=rng.randint(2, 7))
        elif index == 0:
            start_day = as_of_local_day - timedelta(days=365 * 3)
        else:
            start_day = as_of_local_day - timedelta(days=rng.randint(21, 365 * 3))
        valid_from = local_midnight_utc(start_day)
        common = {"source_version": 1, "received_at": iso_z(received_at), "schema_version": SCHEMA_VERSION}
        records["customer_site"].append({
            "customer_id": site_owners[index], "site_id": site_id, "valid_from": iso_z(valid_from),
            "valid_to": None, "relationship_type": "PRIMARY_HOLDER", **common,
        })
        records["site_meter"].append({
            "site_id": site_id, "meter_id": meter_id, "valid_from": iso_z(valid_from), "valid_to": None,
            "change_reason": "INITIAL_INSTALL", **common,
        })
        records["site_contract"].append({
            "contract_id": stable_uuid("contract", index), "site_id": site_id,
            "contract_type": contract_types[site_id], "valid_from": iso_z(valid_from), "valid_to": None, **common,
        })
    return records


def _tariff_grid(received_at: datetime) -> list[dict[str, object]]:
    rules = [
        ("BASE", "ALL_DAYS", None, None, "0.2100"),
        ("WEEKEND", "WEEKDAY", None, None, "0.2400"),
        ("WEEKEND", "WEEKEND", None, None, "0.1800"),
        ("PEAK_OFF_PEAK", "ALL_DAYS", "00:00", "06:00", "0.1500"),
        ("PEAK_OFF_PEAK", "ALL_DAYS", "06:00", "22:00", "0.2800"),
        ("PEAK_OFF_PEAK", "ALL_DAYS", "22:00", "00:00", "0.1500"),
    ]
    return [{
        "tariff_rule_id": stable_uuid("tariff-rule", index), "contract_type": contract_type,
        "valid_from": "2020-01-01T00:00:00Z", "valid_to": None, "day_scope": day_scope,
        "time_from_local": time_from, "time_to_local": time_to, "price_eur_per_kwh": price,
        "source_version": 1, "received_at": iso_z(received_at), "schema_version": SCHEMA_VERSION,
    } for index, (contract_type, day_scope, time_from, time_to, price) in enumerate(rules)]


def iter_consumption_indexes(config: GeneratorConfig, references: dict[str, list[dict[str, object]]]):
    """Yield indexes in deterministic order, streaming a potentially large history."""
    contracts = {str(row["site_id"]): row for row in references["site_contract"]}
    meters = {str(row["site_id"]): row for row in references["site_meter"]}
    rng = random.Random(config.seed + 1)
    for position, site_id in enumerate(sorted(contracts)):
        contract = contracts[site_id]
        meter = meters[site_id]
        cadence = timedelta(days=1) if contract["contract_type"] == "BASE" else timedelta(hours=1)
        reading_at = datetime.fromisoformat(str(contract["valid_from"]).replace("Z", "+00:00"))
        meter_index = REGISTER_MAX_WH - 2_000_000 if position % 37 == 0 else rng.randint(10_000, 10_000_000)
        yield {
            "meter_id": meter["meter_id"], "reading_at": iso_z(reading_at), "meter_index_wh": meter_index,
            "rollover_count": 0, "reading_context": "NORMAL", "source_version": 1,
            "received_at": iso_z(reading_at + timedelta(minutes=rng.randint(2, 9))),
            "schema_version": SCHEMA_VERSION,
        }
        while reading_at <= config.as_of:
            reading_at += cadence
            if reading_at > config.as_of:
                break
            increment = rng.randint(9_000, 22_000) if cadence.days else rng.randint(250, 1_100)
            next_index = meter_index + increment
            rollover_count = 0
            if next_index > REGISTER_MAX_WH:
                next_index -= REGISTER_MAX_WH + 1
                rollover_count = 1
            yield {
                "meter_id": meter["meter_id"], "reading_at": iso_z(reading_at), "meter_index_wh": next_index,
                "rollover_count": rollover_count,
                "reading_context": "NORMAL", "source_version": 1,
                "received_at": iso_z(reading_at + timedelta(minutes=rng.randint(2, 9))),
                "schema_version": SCHEMA_VERSION,
            }
            meter_index = next_index


def write_initial_batch(config: GeneratorConfig) -> dict[str, object]:
    """Write JSON Lines input flows and a manifest, returning their record counts."""
    references = build_reference_records(config)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    files: list[Path] = []
    for flow, rows in references.items():
        path = config.output_dir / "reference" / f"{flow}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
                counts[flow] += 1
        files.append(path)
    if config.include_indexes:
        handles: dict[str, object] = {}
        try:
            for row in iter_consumption_indexes(config, references):
                reading_at = str(row["reading_at"])
                month = reading_at[:7]
                path = config.output_dir / "consumption_index" / f"reading_month={month}" / "indexes.jsonl"
                if month not in handles:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    handles[month] = path.open("a", encoding="utf-8", newline="\n")
                    files.append(path)
                handles[month].write(json.dumps(row, sort_keys=True) + "\n")
                counts["consumption_index"] += 1
        finally:
            for handle in handles.values():
                handle.close()
    manifest = {
        "batch_type": "INITIAL", "generator_version": "0.1.0", "seed": config.seed,
        "as_of": iso_z(config.as_of), "schema_version": SCHEMA_VERSION, "record_counts": dict(sorted(counts.items())),
        "files": [{"path": str(path.relative_to(config.output_dir)), "sha256": _sha256(path)} for path in sorted(files)],
    }
    (config.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
