"""Local reference oracle for idempotence and interval derivation."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .intervals import ConsumptionIndex, InvalidIndexSequence, derive_consumption_interval


def _index_paths(batch_dirs: list[Path]) -> list[Path]:
    return sorted(path for directory in batch_dirs for path in directory.rglob("*.jsonl") if "consumption_index" in path.as_posix())


def process_indexes(batch_dirs: list[Path]) -> dict[str, list[dict[str, object]]]:
    """Keep the highest source version per business key, then derive intervals.

    This is a readable local oracle for the later Spark implementation, not the
    intended engine for large-scale cloud processing.
    """
    latest: dict[tuple[str, str], dict[str, object]] = {}
    for path in _index_paths(batch_dirs):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                key = (row["meter_id"], row["reading_at"])
                existing = latest.get(key)
                if existing is None or (int(row["source_version"]), row["received_at"]) > (int(existing["source_version"]), existing["received_at"]):
                    latest[key] = row

    by_meter: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in latest.values():
        by_meter[row["meter_id"]].append(row)
    intervals: list[dict[str, object]] = []
    quarantined: list[dict[str, object]] = []
    for meter_id, rows in by_meter.items():
        previous = None
        for row in sorted(rows, key=lambda item: item["reading_at"]):
            current = ConsumptionIndex(
                meter_id=meter_id,
                reading_at=datetime.fromisoformat(row["reading_at"].replace("Z", "+00:00")),
                meter_index_wh=int(row["meter_index_wh"]), rollover_count=int(row["rollover_count"]),
                reading_context=row["reading_context"],
            )
            try:
                interval = derive_consumption_interval(previous, current)
            except InvalidIndexSequence as error:
                quarantined.append({**row, "quarantine_reason": str(error)})
                continue
            if interval is not None:
                intervals.append({
                    "meter_id": interval.meter_id, "interval_start": interval.interval_start.isoformat().replace("+00:00", "Z"),
                    "interval_end": interval.interval_end.isoformat().replace("+00:00", "Z"), "consumption_wh": interval.consumption_wh,
                    "quality_status": interval.quality_status, "allocation_method": interval.allocation_method,
                })
            previous = current
    return {"validated_indexes": sorted(latest.values(), key=lambda row: (row["meter_id"], row["reading_at"])),
            "consumption_intervals": sorted(intervals, key=lambda row: (row["meter_id"], row["interval_end"])),
            "quarantine": quarantined}
