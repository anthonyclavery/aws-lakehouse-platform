"""Generation of deterministic incremental meter-index batches."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .generator import REGISTER_MAX_WH, SCHEMA_VERSION, iso_z


def _rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def latest_indexes(initial_batch_dir: Path) -> dict[str, dict[str, object]]:
    """Stream a generated initial batch and retain only the newest index per meter."""
    latest: dict[str, dict[str, object]] = {}
    for path in sorted((initial_batch_dir / "consumption_index").rglob("*.jsonl")):
        for row in _rows(path):
            meter_id = row["meter_id"]
            if meter_id not in latest or row["reading_at"] > latest[meter_id]["reading_at"]:
                latest[meter_id] = row
    if not latest:
        raise ValueError("no consumption indexes found in initial_batch_dir")
    return latest


def generate_incremental_batch(
    initial_batch_dir: Path,
    output_dir: Path,
    until: datetime,
    seed: int = 20261017,
) -> dict[str, object]:
    """Continue each meter from its last delivered cumulative index until ``until``."""
    latest = latest_indexes(initial_batch_dir)
    contracts = {row["site_id"]: row for row in _rows(initial_batch_dir / "reference" / "site_contract.jsonl")}
    meters = {row["meter_id"]: row["site_id"] for row in _rows(initial_batch_dir / "reference" / "site_meter.jsonl")}
    rng = random.Random(seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "consumption_index.jsonl"
    count = 0
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for meter_id in sorted(latest):
            current = latest[meter_id]
            contract_type = contracts[meters[meter_id]]["contract_type"]
            cadence = timedelta(days=1) if contract_type == "BASE" else timedelta(hours=1)
            reading_at = datetime.fromisoformat(current["reading_at"].replace("Z", "+00:00"))
            meter_index = int(current["meter_index_wh"])
            while reading_at + cadence <= until.astimezone(UTC):
                reading_at += cadence
                increment = rng.randint(9_000, 22_000) if contract_type == "BASE" else rng.randint(250, 1_100)
                meter_index += increment
                rollover_count = 0
                if meter_index > REGISTER_MAX_WH:
                    meter_index -= REGISTER_MAX_WH + 1
                    rollover_count = 1
                row = {
                    "meter_id": meter_id, "reading_at": iso_z(reading_at), "meter_index_wh": meter_index,
                    "rollover_count": rollover_count, "reading_context": "NORMAL", "source_version": 1,
                    "received_at": iso_z(reading_at + timedelta(minutes=rng.randint(2, 9))),
                    "schema_version": SCHEMA_VERSION,
                }
                handle.write(json.dumps(row, sort_keys=True) + "\n")
                count += 1
    manifest = {"batch_type": "INCREMENTAL", "until": iso_z(until), "seed": seed, "record_counts": {"consumption_index": count}}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
