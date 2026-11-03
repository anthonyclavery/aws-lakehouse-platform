"""Read-only Athena reconciliation against the shared local Gold calculator."""

from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import json
import time

from .glue_runner import build_gold_arguments
from .pricing import gold_products, iter_priced_segments
from .processing import process_indexes
from .reference_processing import process_reference_data

DIMENSIONS = {
    "daily_site_energy": ("customer_id", "site_id", "local_date"),
    "daily_customer_energy": ("customer_id", "local_date"),
    "monthly_customer_energy": ("customer_id", "local_month"),
}
MEASURES = ("consumption_kwh", "energy_cost_eur")


def expected_products(batch_dirs):
    silver = process_indexes(batch_dirs)
    references = process_reference_data(batch_dirs)
    segments = iter_priced_segments(silver["consumption_intervals"], references)
    # Match the persisted Glue segment scale before aggregating.
    rounded = ({**row, **{key: row[key].quantize(Decimal("1e-12"), rounding=ROUND_HALF_EVEN)
                         for key in MEASURES}} for row in segments)
    return gold_products(rounded)


def query_rows(athena, database, workgroup, sql, *, timeout_seconds=300, poll_seconds=2):
    build_gold_arguments(database)  # Validate database before it enters SQL.
    query_id = athena.start_query_execution(
        QueryString=sql, WorkGroup=workgroup,
        ResultReuseConfiguration={"ResultReuseByAgeConfiguration": {"Enabled": False}},
        QueryExecutionContext={"Database": database, "Catalog": "AwsDataCatalog"})["QueryExecutionId"]
    deadline = time.monotonic() + timeout_seconds
    while True:
        execution = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]
        state = execution["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in {"FAILED", "CANCELLED"}:
            raise RuntimeError(f"Athena query {query_id}: {execution['Status'].get('StateChangeReason', state)}")
        if time.monotonic() >= deadline:
            athena.stop_query_execution(QueryExecutionId=query_id)
            raise TimeoutError(f"Athena query {query_id} exceeded the qualification time bound")
        time.sleep(poll_seconds)
    rows, token, first_page = [], None, True
    while True:
        arguments = {"QueryExecutionId": query_id, "MaxResults": 1000}
        if token:
            arguments["NextToken"] = token
        page = athena.get_query_results(**arguments)
        result = page["ResultSet"]
        columns = [column["Name"] for column in result["ResultSetMetadata"]["ColumnInfo"]]
        values = result["Rows"][1:] if first_page else result["Rows"]
        rows.extend(dict(zip(columns, [cell.get("VarCharValue") for cell in row["Data"]])) for row in values)
        first_page, token = False, page.get("NextToken")
        if not token:
            return rows, execution.get("Statistics", {})


def compare_product(table, expected, actual, *, tolerance=Decimal("1e-9")):
    dimensions = DIMENSIONS[table]

    def indexed(rows):
        result = {}
        for row in rows:
            key = tuple(str(row[field]) for field in dimensions)
            if key in result:
                raise ValueError(f"Duplicate group in {table}")
            result[key] = {field: Decimal(str(row[field])) for field in MEASURES}
            if any(not number.is_finite() for number in result[key].values()):
                raise ValueError(f"Non-finite measure in {table}")
        return result

    local, cloud = indexed(expected), indexed(actual)
    shared = local.keys() & cloud.keys()
    max_errors = {field: max((abs(local[key][field] - cloud[key][field]) for key in shared), default=Decimal(0))
                  for field in MEASURES}
    missing, extra = len(local.keys() - cloud.keys()), len(cloud.keys() - local.keys())

    def fingerprint(groups):
        normalized = [[*key, *(format(values[field], ".12f") for field in MEASURES)]
                      for key, values in sorted(groups.items())]
        return hashlib.sha256(json.dumps(normalized, separators=(",", ":")).encode()).hexdigest()

    return {"expected_rows": len(local), "actual_rows": len(cloud), "missing_groups": missing, "extra_groups": extra,
            "max_absolute_error": {k: str(v) for k,v in max_errors.items()},
            "expected_sha256": fingerprint(local), "actual_sha256": fingerprint(cloud),
            "passed": not missing and not extra and all(error <= tolerance for error in max_errors.values())}


def qualify_gold(athena, database, workgroup, batch_dirs):
    build_gold_arguments(database)
    expected = expected_products(batch_dirs)
    report = {"products": {}, "bytes_scanned": 0, "engine_execution_ms": 0}
    for table, dimensions in DIMENSIONS.items():
        columns = ", ".join(f"CAST({field} AS varchar) AS {field}" for field in (*dimensions, *MEASURES))
        actual, statistics = query_rows(athena, database, workgroup, f'SELECT {columns} FROM "{database}"."{table}"')
        report["products"][table] = compare_product(table, expected[table], actual)
        report["bytes_scanned"] += statistics.get("DataScannedInBytes", 0)
        report["engine_execution_ms"] += statistics.get("EngineExecutionTimeInMillis", 0)
    report["passed"] = all(product["passed"] for product in report["products"].values())
    return report
