"""Disposable canonical Gold/Silver fixture for actual analyst access tests.

Run only in the empty project demonstration database, before enabling analyst
governance. Refuses any existing canonical table; does not overwrite data.
"""
import json
import sys
from datetime import datetime, timedelta


def main():
    from awsglue.context import GlueContext
    from awsglue.utils import getResolvedOptions
    from pyspark.context import SparkContext
    from pyspark.sql import functions as F
    from gold_refresh import price, aggregate
    database = getResolvedOptions(sys.argv, ["DATABASE"])["DATABASE"]
    if not __import__("re").fullmatch(r"aws_lakehouse_platform_[a-z0-9_]+", database):
        raise ValueError("Expected isolated project database")
    names = {"INTERVAL_TABLE": "meter_consumption_interval", "SITE_METER_TABLE": "site_meter_history",
             "CUSTOMER_SITE_TABLE": "customer_site_history", "SITE_CONTRACT_TABLE": "site_contract_history",
             "TARIFF_TABLE": "tariff_grid_history", "PRICED_INTERVAL_TABLE": "priced_intervals",
             "DAILY_SITE_TABLE": "daily_site_energy", "DAILY_CUSTOMER_TABLE": "daily_customer_energy",
             "MONTHLY_CUSTOMER_TABLE": "monthly_customer_energy"}
    args = {k: f"glue_catalog.{database}.{v}" for k, v in names.items()}
    args.update(BATCH_ID="analyst-initial", REFRESH_MODE="bounded")
    spark = GlueContext(SparkContext.getOrCreate()).spark_session
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.ansi.enabled", "true")
    spark.conf.set("spark.sql.shuffle.partitions", "4")
    tables = [v for k, v in args.items() if k.endswith("_TABLE")] + [f"glue_catalog.{database}.consumption_index", f"glue_catalog.{database}.quarantine_records"]
    if any(spark.catalog.tableExists(t) for t in tables):
        raise ValueError("Canonical table already exists; use an empty demonstration")
    valid = {"valid_from": "2020-01-01T00:00:00Z", "valid_to": None}
    refs = {
        "SITE_METER_TABLE": ([{**valid, "meter_id": f"m{i}", "site_id": f"s{i}"} for i in range(2)],
                             "meter_id string, site_id string, valid_from string, valid_to string"),
        "CUSTOMER_SITE_TABLE": ([{**valid, "site_id": f"s{i}", "customer_id": f"c{i}"} for i in range(2)],
                                "site_id string, customer_id string, valid_from string, valid_to string"),
        "SITE_CONTRACT_TABLE": ([{**valid, "site_id": f"s{i}", "contract_type": "BASE"} for i in range(2)],
                                "site_id string, contract_type string, valid_from string, valid_to string"),
        "TARIFF_TABLE": ([{**valid, "contract_type": "BASE", "day_scope": "ALL_DAYS", "time_from_local": None,
                           "time_to_local": None, "price_eur_per_kwh": "0.20"}],
                         "contract_type string, day_scope string, time_from_local string, time_to_local string, price_eur_per_kwh string, valid_from string, valid_to string"),
    }
    for key, (rows, schema) in refs.items():
        df = spark.createDataFrame(rows, schema)
        for field in ("valid_from", "valid_to"):
            df = df.withColumn(field, F.to_timestamp(field))
        df.writeTo(args[key]).using("iceberg").create()
    start = datetime(2026, 1, 5)
    rows = [(f"m{i}", start + timedelta(days=d), start + timedelta(days=d, hours=1), (i + 1) * 1000, "VALID")
            for i in range(2) for d in range(2)]
    spark.createDataFrame(rows, "meter_id string, interval_start timestamp, interval_end timestamp, consumption_wh long, quality_status string")\
        .writeTo(args["INTERVAL_TABLE"]).using("iceberg").create()
    spark.createDataFrame([(f"m{i}", start, 1000, 1) for i in range(2)],
                         "meter_id string, reading_at timestamp, meter_index_wh long, source_version int")\
        .writeTo(f"glue_catalog.{database}.consumption_index").using("iceberg").create()
    spark.createDataFrame([("analyst-initial", r[0], r[2], "DONE") for r in rows],
                         "batch_id string, meter_id string, reading_at timestamp, state string")\
        .writeTo(args["INTERVAL_TABLE"] + "_recalculation_work").using("iceberg").create()
    spark.createDataFrame([], "meter_id string, reading_at timestamp, meter_index_wh long, rollover_count int, reading_context string, source_version int, received_at timestamp, schema_version int, quarantine_reason string")\
        .writeTo(f"glue_catalog.{database}.quarantine_records").using("iceberg").create()
    price(spark, args)
    aggregate(spark, args)
    print("ANALYST_FIXTURE " + json.dumps({"intervals": 4, "daily_site_rows": 4, "daily_customer_rows": 4,
                                           "monthly_customer_rows": 2, "total_kwh": "6", "total_eur": "1.2"}), flush=True)


if __name__ == "__main__":
    main()
