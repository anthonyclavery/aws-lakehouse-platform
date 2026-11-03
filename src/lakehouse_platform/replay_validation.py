"""Content and Iceberg metadata identity checks for genuine processing replays."""
import hashlib
import re

from .analyst_access import GOLD, validate_database

SILVER_TABLES = frozenset({"consumption_index", "meter_consumption_interval", "site_meter_history",
                           "customer_site_history", "site_contract_history", "tariff_grid_history"})
REPLAY_TABLES = SILVER_TABLES | frozenset(GOLD) | {"priced_intervals", "quarantine_records"}


def capture_replay_metadata(glue, database, bucket):
    validate_database(database)
    if not re.fullmatch(r"aws-lakehouse-platform-[a-z0-9-]{3,32}-[a-f0-9]{8}", bucket):
        raise ValueError("Expected an isolated demonstration bucket")
    metadata = {}
    for table in sorted(REPLAY_TABLES):
        parameters = glue.get_table(DatabaseName=database, Name=table)["Table"]["Parameters"]
        location = parameters.get("metadata_location", "")
        if parameters.get("table_type", "").upper() != "ICEBERG" or not location.startswith(f"s3://{bucket}/warehouse/"):
            raise ValueError("Every replay data table must expose an existing project Iceberg metadata location")
        metadata[table] = hashlib.sha256(location.encode()).hexdigest()
    return metadata


def compare_replay(silver_before, silver_after, analyst_before, analyst_after, metadata_before, metadata_after):
    for silver in (silver_before, silver_after):
        if not silver["passed"] or set(silver["tables"]) != SILVER_TABLES:
            raise ValueError("Replay requires successful complete Silver comparisons")
    for analyst in (analyst_before, analyst_after):
        if (not analyst["passed"] or set(analyst["gold_products"]) != set(GOLD) or analyst["results_reused"]
                or not analyst["temporary_credentials_used"] or not analyst["gold_select_restored"]):
            raise ValueError("Replay requires successful fresh real-analyst comparisons")
    for rows in (silver_before["tables"], silver_after["tables"], analyst_before["gold_products"], analyst_after["gold_products"]):
        if any(not isinstance(row["actual_rows"], int) or row["actual_rows"] < 1 or
               not re.fullmatch(r"[a-f0-9]{64}", row["actual_sha256"]) for row in rows.values()):
            raise ValueError("Replay content evidence must contain nonempty rows and actual fingerprints")
    for metadata in (metadata_before, metadata_after):
        if set(metadata) != REPLAY_TABLES or any(not re.fullmatch(r"[a-f0-9]{64}", value) for value in metadata.values()):
            raise ValueError("Replay metadata evidence must cover every contracted data table")
    silver_stable = {table: silver_before["tables"][table]["actual_sha256"] == silver_after["tables"][table]["actual_sha256"]
                     and silver_before["tables"][table]["actual_rows"] == silver_after["tables"][table]["actual_rows"] for table in SILVER_TABLES}
    gold_stable = {table: analyst_before["gold_products"][table]["actual_sha256"] == analyst_after["gold_products"][table]["actual_sha256"]
                   and analyst_before["gold_products"][table]["actual_rows"] == analyst_after["gold_products"][table]["actual_rows"] for table in GOLD}
    metadata_stable = {table: metadata_before[table] == metadata_after[table] for table in REPLAY_TABLES}
    return {"passed": all(silver_stable.values()) and all(gold_stable.values()) and all(metadata_stable.values()),
            "silver_content_and_versions_unchanged": silver_stable, "gold_content_unchanged": gold_stable,
            "iceberg_data_table_metadata_unchanged": metadata_stable,
            "control_and_stage_tables_excluded_from_metadata_stability": True}
