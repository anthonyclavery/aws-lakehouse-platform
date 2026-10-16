"""Generate deterministic late and corrected index events for demonstrations."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path

from .generator import SCHEMA_VERSION, iso_z


def _index_paths(batch_dir: Path) -> list[Path]:
    return sorted(path for path in batch_dir.rglob("*.jsonl") if "consumption_index" in path.as_posix())


def _rows_for_meter(batch_dir: Path, meter_id: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in _index_paths(batch_dir):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    if row["meter_id"] == meter_id:
                        rows.append(row)
    return sorted(rows, key=lambda row: row["reading_at"])


def _select_meter(batch_dir: Path, seed: int) -> str:
    meter_ids: set[str] = set()
    for path in _index_paths(batch_dir):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    meter_ids.add(json.loads(line)["meter_id"])
    if not meter_ids:
        raise ValueError("no consumption indexes found in source_batch_dir")
    return random.Random(seed).choice(sorted(meter_ids))


def generate_exception_event(
    source_batch_dir: Path,
    output_dir: Path,
    kind: str,
    received_at: datetime,
    seed: int = 20261018,
) -> dict[str, object]:
    """Write one late or corrected event and a small, traceable manifest.

    A correction supersedes one existing business key with ``source_version + 1``.
    A late event is inserted halfway between two known indexes of the same meter.
    """
    if kind not in {"late", "correction"}:
        raise ValueError("kind must be 'late' or 'correction'")
    meter_id = _select_meter(source_batch_dir, seed)
    rows = _rows_for_meter(source_batch_dir, meter_id)
    if len(rows) < 3:
        raise ValueError("selected meter needs at least three source indexes")
    middle = len(rows) // 2
    output_dir.mkdir(parents=True, exist_ok=True)

    if kind == "correction":
        candidates = [index for index in range(1, len(rows) - 1)
                      if rows[index]["rollover_count"] == 0
                      and int(rows[index + 1]["meter_index_wh"]) > int(rows[index]["meter_index_wh"])]
        index = min(candidates, key=lambda item: abs(item - middle))
        original = rows[index]
        next_row = rows[index + 1]
        safe_delta = max(1, min(100, (int(next_row["meter_index_wh"]) - int(original["meter_index_wh"])) // 4))
        event = {**original, "meter_index_wh": int(original["meter_index_wh"]) + safe_delta,
                 "source_version": int(original["source_version"]) + 1, "received_at": iso_z(received_at)}
    else:
        candidates = [index for index in range(len(rows) - 1)
                      if rows[index + 1]["rollover_count"] == 0
                      and int(rows[index + 1]["meter_index_wh"]) > int(rows[index]["meter_index_wh"])]
        index = min(candidates, key=lambda item: abs(item - middle))
        previous, following = rows[index], rows[index + 1]
        start = datetime.fromisoformat(previous["reading_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(following["reading_at"].replace("Z", "+00:00"))
        event = {
            "meter_id": meter_id, "reading_at": iso_z(start + (end - start) / 2),
            "meter_index_wh": (int(previous["meter_index_wh"]) + int(following["meter_index_wh"])) // 2,
            "rollover_count": 0, "reading_context": "NORMAL", "source_version": 1,
            "received_at": iso_z(received_at), "schema_version": SCHEMA_VERSION,
        }

    path = output_dir / f"{kind}_consumption_index.jsonl"
    path.write_text(json.dumps(event, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {"batch_type": kind.upper(), "seed": seed, "received_at": iso_z(received_at),
                "record_counts": {"consumption_index": 1}, "meter_id": meter_id}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest
