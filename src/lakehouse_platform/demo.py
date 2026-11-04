"""One explicit, journalled demonstration journey, with an offline preview."""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re

from .analyst_queries import QUERIES, render_query
from .generator import stable_uuid
from .late_chain_fixture import write_late_chain_fixture
from .processing import process_indexes
from .qualification import compare_product, expected_products

PHASES = (("initial", "initial", ("initial",), True),
          ("late", "late", ("initial", "late"), False),
          ("correction", "correction", ("initial", "late", "correction"), False),
          ("unchanged-replay", "correction", ("initial", "late", "correction"), False),
          ("older-replay", "initial", ("initial", "late", "correction"), True))


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def fixture_digest(workspace):
    digest = hashlib.sha256()
    for path in sorted((workspace / "batches").rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(workspace).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def load_state(workspace):
    state = read(workspace / "state.json")
    if state.get("version") != 1 or fixture_digest(workspace) != state["fixture_sha256"]:
        raise ValueError("Unknown workspace or changed source fixture; preserve it and prepare a fresh workspace")
    return state


@contextmanager
def locked(workspace):
    """Do not submit overlapping runs or steal a lock after a client crash."""
    lock = workspace / ".operation.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        yield
    finally:
        lock.unlink()


def prepare(workspace, run_id):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,39}", run_id):
        raise ValueError("Run ID requires 3-40 lowercase letters, digits or hyphens")
    workspace.mkdir(parents=True, exist_ok=False, mode=0o700)
    fixture = write_late_chain_fixture(workspace / "batches")
    state = {"version": 1, "run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
             "fixture_sha256": fixture_digest(workspace), "aws_status": "prepared", "local_status": "prepared"}
    write(workspace / "fixture.json", fixture)
    write(workspace / "state.json", state)
    # Render examples against a deliberately non-deployed database. Actual query
    # execution always uses validated live deployment outputs instead.
    queries = workspace / "queries"
    queries.mkdir()
    for name in QUERIES:
        bounds = ("2026-01-01", "2026-02-01") if name == "monthly-customer" else ("2026-01-09", "2026-01-11")
        (queries / (name + ".sql")).write_text(render_query(name, "aws_lakehouse_platform_example", *bounds))
    return state


def summarize_phase(products, batches, *, cloud=False, checks=None):
    expected = expected_products(batches)
    comparisons = {table: compare_product(table, expected[table], products[table]) for table in expected}
    if not all(row["passed"] for row in comparisons.values()):
        raise ValueError("A demonstrated Gold product differs from the expected complete contents")
    silver = process_indexes(batches)
    monthly = products["monthly_customer_energy"]
    return {"passed": True, "origin": "AWS analyst" if cloud else "local oracle",
            "indexes": len(silver["validated_indexes"]), "intervals": len(silver["consumption_intervals"]),
            "raw_energy_wh": sum(row["consumption_wh"] for row in silver["consumption_intervals"]),
            "gold_kwh": str(sum(Decimal(str(row["consumption_kwh"])) for row in monthly)),
            "gold_eur": str(sum(Decimal(str(row["energy_cost_eur"])) for row in monthly)),
            "contract_eur": {name: str(sum(Decimal(str(row["energy_cost_eur"])) for row in monthly
                if row["customer_id"] == stable_uuid("integrated-customer", index)))
                for index, name in enumerate(("BASE", "WEEKEND", "PEAK_OFF_PEAK"))},
            "products": comparisons, "checks": checks or {"AWS_access_and_metadata": "not measured locally"}}


def render_report(report):
    mode = report["mode"]
    number = lambda value: format(Decimal(value), ".6f")
    lines = ["# Smart-meter demonstration", "", f"Run: **{report['run_id']}**. Mode: **{mode}**. Status: **{report['status']}**.", "",
             "Synthetic fixture: three customers/sites, BASE, WEEKEND and PEAK_OFF_PEAK. Dates use Europe/Paris.", "",
             "Local mode computes expected business results; it does not measure AWS jobs, permissions or Iceberg snapshots."
             if mode == "local" else "AWS values are read using actual temporary analyst credentials and reconciled against the complete local history.",
             "", "| Phase | Indexes | Intervals | Raw Wh | Gold kWh | Energy cost EUR |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    ordered = [(phase[0], report["phases"][phase[0]]) for phase in PHASES if phase[0] in report["phases"]]
    for name, row in ordered:
        lines.append(f"| {name} | {row['indexes']} | {row['intervals']} | {row['raw_energy_wh']} | {number(row['gold_kwh'])} | {number(row['gold_eur'])} |")
    lines += ["", "| Phase | BASE EUR | WEEKEND EUR | PEAK_OFF_PEAK EUR |", "| --- | ---: | ---: | ---: |"]
    for name, row in ordered:
        lines.append(f"| {name} | " + " | ".join(number(row["contract_eur"][key]) for key in ("BASE", "WEEKEND", "PEAK_OFF_PEAK")) + " |")
    lines += ["", "The late reading splits an existing interval. The correction changes its endpoint and successor while conserving 30,000 Wh.",
              "Gold quantizes each priced segment to twelve decimals; the 0.000000000003 kWh residue is rounding, not additional raw energy.",
              "Tables display six decimal places; the companion JSON retains exact measured decimal values.",
              "Both replays must retain corrected versions and complete product contents.", "", "## Checks", ""]
    for name, row in ordered:
        checks = "; ".join(f"{key}: {value}" for key, value in row["checks"].items())
        lines.append(f"{name}: {checks}.")
        lines.append("")
    if report["status"] != "completed":
        lines += ["The journey is incomplete. Preserve the private journal and inspect the stopped stage before another AWS launch.", ""]
    lines += ["Costs cover synthetic energy only, excluding subscription charges and taxes. AWS service charges are not measured by this report.",
              "Data-plane cleanup is a separate explicit command; this report does not certify destruction.", ""]
    return "\n".join(lines)


def save_report(workspace, report):
    write(workspace / (report["mode"] + "-report.json"), report)
    path = workspace / (report["mode"] + "-report.md")
    path.write_text(render_report(report))


def run_scenarios(workspace, execute=None):
    """Fail once, preserve partial evidence and never resubmit an uncertain AWS run."""
    state = load_state(workspace)
    mode = "aws" if execute else "local"
    key = mode + "_status"
    required = "deployed" if execute else "prepared"
    if state[key] != required:
        raise ValueError("This journey has already started or lacks its prerequisite; inspect status and preserve its journal")
    report = {"run_id": state["run_id"], "mode": mode, "status": "running", "phases": {}}
    state[key] = "running"
    write(workspace / "state.json", state)
    save_report(workspace, report)
    try:
        for phase, source, history, references in PHASES:
            print(f"[{mode}] {phase}: starting", flush=True)
            state["active_phase"] = phase
            write(workspace / "state.json", state)
            batches = [workspace / "batches" / name for name in history]
            if execute:
                products, checks = execute(phase, source, batches, references)
            else:
                replay = [workspace / "batches" / source] if phase.endswith("replay") else []
                products, checks = expected_products([*batches, *replay]), None
                if replay and process_indexes(batches) != process_indexes([*batches, *replay]):
                    raise ValueError("Replay changed the complete Silver contents")
            report["phases"][phase] = summarize_phase(products, batches, cloud=bool(execute), checks=checks)
            save_report(workspace, report)
            print(f"[{mode}] {phase}: reconciled", flush=True)
    except Exception:
        state[key], report["status"] = "failed", "failed"
        write(workspace / "state.json", state)
        save_report(workspace, report)
        raise
    state[key], report["status"] = "completed", "completed"
    state.pop("active_phase", None)
    write(workspace / "state.json", state)
    save_report(workspace, report)
    return report
