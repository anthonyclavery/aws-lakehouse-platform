"""Small deterministic batches for bounded Silver and recovery qualification."""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from .generator import _sha256, stable_uuid


def bounded_fixture_batches():
    start = datetime(2026, 9, 1, tzinfo=UTC)

    def reading(hour, meter=0, version=1, value=None, context="NORMAL"):
        return {
            "meter_id":stable_uuid("bounded-meter",meter),
            "reading_at":(start+timedelta(hours=hour)).isoformat().replace("+00:00","Z"),
            "meter_index_wh":int(1000+hour*(1000 if meter==0 else 700)) if value is None else value,
            "rollover_count":0,"reading_context":context,"source_version":version,
            "received_at":"2026-10-20T00:00:00Z","schema_version":1,
        }

    return {
        "initial":[reading(hour,meter) for meter in range(2) for hour in range(1000)],
        "append":[reading(1000),reading(1001)],
        "delta":[reading(500,version=2,value=501100),reading(500.5)],
        "fault":[reading(600,version=2,value=601100)],
        "blocked":[reading(1002)],
        "baseline":[reading(700,version=2,context="INSTALLATION_BASELINE")],
        "invalid":[reading(1003,value=-1),reading(1004,context=None)],
        "sequence-invalid":[reading(750,version=2,value=1)],
    }


def write_bounded_fixture(output_dir: Path):
    output_dir.mkdir(parents=True,exist_ok=False)
    counts = {}
    for name,rows in bounded_fixture_batches().items():
        directory = output_dir/name
        directory.mkdir()
        path = directory/"consumption_index.jsonl"
        path.write_text("".join(json.dumps(row,sort_keys=True)+"\n" for row in rows),encoding="utf-8",newline="\n")
        manifest = {"files":[{"path":path.name,"sha256":_sha256(path)}],"record_counts":{"consumption_index":len(rows)}}
        (directory/"manifest.json").write_text(json.dumps(manifest,sort_keys=True)+"\n",encoding="utf-8",newline="\n")
        counts[name] = len(rows)
    return counts
