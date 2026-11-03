"""Full-row Silver reconciliation and completed-work checks for the demo chain."""
from datetime import UTC, datetime
import hashlib
import json

from .analyst_access import validate_database
from .processing import process_indexes
from .qualification import query_rows
from .reference_processing import process_reference_data

INDEX_FIELDS = ("meter_id", "reading_at", "meter_index_wh", "rollover_count", "reading_context", "source_version", "received_at", "schema_version")
INTERVAL_FIELDS = ("meter_id", "interval_start", "interval_end", "consumption_wh", "quality_status")
REFERENCE_FIELDS = {
    "site_meter": ("site_id", "meter_id"), "customer_site": ("site_id", "customer_id"),
    "site_contract": ("contract_id", "site_id", "contract_type"),
    "tariff_grid": ("tariff_rule_id", "contract_type", "day_scope", "time_from_local", "time_to_local", "price_eur_per_kwh"),
}
TIMESTAMPS = frozenset({"reading_at", "received_at", "interval_start", "interval_end", "valid_from", "valid_to"})


def compare_silver_rows(expected, actual, fields):
    def normalize(rows):
        result = []
        for row in rows:
            values = []
            for field in fields:
                value = row[field]
                if value is not None and field in TIMESTAMPS:
                    encoded = str(value)
                    if encoded.endswith(" UTC"):
                        encoded = encoded[:-4] + "+00:00"
                    stamp = datetime.fromisoformat(encoded.replace("Z", "+00:00"))
                    if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=UTC)
                    value = stamp.astimezone(UTC).isoformat()
                values.append(None if value is None else str(value))
            result.append(values)
        return sorted(result, key=lambda value: json.dumps(value))
    local, cloud = normalize(expected), normalize(actual)
    digest = lambda value: hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()
    return {"passed": local == cloud, "expected_rows": len(local), "actual_rows": len(cloud),
            "expected_sha256": digest(local), "actual_sha256": digest(cloud)}


def qualify_integrated_silver(athena, database, workgroup, batch_dirs):
    validate_database(database)
    local = process_indexes(batch_dirs)
    references = process_reference_data(batch_dirs)
    if not local["consumption_intervals"] or local["quarantine"] or any(not rows for rows in references.values()):
        raise ValueError("The positive chain audit requires nonempty references/intervals and no local quarantine")
    tables = {"consumption_index": (local["validated_indexes"], INDEX_FIELDS),
              "meter_consumption_interval": (local["consumption_intervals"], INTERVAL_FIELDS)}
    for flow, fields in REFERENCE_FIELDS.items():
        tables[flow + "_history"] = (references[flow], (*fields, "valid_from", "valid_to", "source_version", "received_at", "schema_version"))
    comparisons = {}
    for table, (expected, fields) in tables.items():
        columns = ", ".join(f'CAST("{field}" AS varchar) AS "{field}"' for field in fields)
        actual, _ = query_rows(athena, database, workgroup, f'SELECT {columns} FROM "{database}"."{table}"')
        comparisons[table] = compare_silver_rows(expected, actual, fields)
    counts = {}
    for table in ("quarantine_records", "meter_consumption_interval_recalculation_work", "priced_intervals_refresh"):
        where = "" if table == "quarantine_records" else " WHERE state IS NULL OR state <> 'DONE'"
        rows, _ = query_rows(athena, database, workgroup, f'SELECT CAST(COUNT(*) AS varchar) AS n FROM "{database}"."{table}"{where}')
        counts[table] = int(rows[0]["n"])
    for table in ("meter_consumption_interval_recalculation_work", "priced_intervals_refresh"):
        rows, _ = query_rows(athena, database, workgroup, f'SELECT CAST(COUNT(*) AS varchar) AS n FROM "{database}"."{table}"')
        if int(rows[0]["n"]) < 1:
            raise ValueError("Completed Silver/Gold work evidence must not be empty")
    return {"passed": all(value["passed"] for value in comparisons.values()) and not any(counts.values()),
            "tables": comparisons, "quarantine_and_unfinished_counts": counts,
            "expected_intervals": len(local["consumption_intervals"]),
            "expected_consumption_wh": sum(int(row["consumption_wh"]) for row in local["consumption_intervals"])}
