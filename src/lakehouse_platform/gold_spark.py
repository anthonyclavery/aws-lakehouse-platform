"""Glue adapters for the shared pricing module (shipped as --extra-py-files)."""

from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_HALF_EVEN

from pricing import iter_priced_segments

SEGMENT_COLUMNS = (
    "meter_id", "customer_id", "site_id", "interval_start", "interval_end",
    "segment_start", "segment_end", "local_date", "contract_type",
    "price_eur_per_kwh", "consumption_kwh", "energy_cost_eur",
    "allocation_method", "quality_status",
)
SEGMENT_SCHEMA = """meter_id string, customer_id string, site_id string,
interval_start timestamp, interval_end timestamp, segment_start timestamp, segment_end timestamp,
local_date date, contract_type string, price_eur_per_kwh decimal(24,12),
consumption_kwh decimal(24,12), energy_cost_eur decimal(24,12),
allocation_method string, quality_status string"""
REFERENCE_LIMIT = 10000


def utc_record(row):
    """Spark timestamps arrive naive; the job pins its session timezone to UTC."""
    row = row.asDict() if hasattr(row, "asDict") else dict(row)
    for key in ("valid_from", "valid_to", "interval_start", "interval_end"):
        value = row.get(key)
        if value is not None and hasattr(value, "tzinfo") and value.tzinfo is None:
            row[key] = value.replace(tzinfo=UTC)
    return row


def price_partition(rows, references):
    for segment in iter_priced_segments((utc_record(row) for row in rows), references):
        for key in ("interval_start", "interval_end", "segment_start", "segment_end"):
            segment[key] = datetime.fromisoformat(segment[key])
        segment["local_date"] = date.fromisoformat(segment["local_date"])
        yield tuple(segment[key].quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN)
                    if isinstance(segment[key], Decimal) else segment[key] for key in SEGMENT_COLUMNS)


def rebuild_priced(spark, args):
    references = {}
    for flow, argument in (("site_meter", "SITE_METER_TABLE"), ("customer_site", "CUSTOMER_SITE_TABLE"),
                           ("site_contract", "SITE_CONTRACT_TABLE"), ("tariff_grid", "TARIFF_TABLE")):
        rows = spark.table(args[argument]).limit(REFERENCE_LIMIT + 1).collect()
        if len(rows) > REFERENCE_LIMIT:
            raise ValueError(f"{flow} exceeds demo broadcast limit {REFERENCE_LIMIT}")
        references[flow] = [utc_record(row) for row in rows]
    broadcast = spark.sparkContext.broadcast(references)
    priced = spark.createDataFrame(
        spark.table(args["INTERVAL_TABLE"]).rdd.mapPartitions(
            lambda rows: price_partition(rows, broadcast.value)), SEGMENT_SCHEMA).cache()
    try:
        # Force all business validation before the atomic Iceberg replacement.
        count = priced.count()
        priced.writeTo(args["PRICED_INTERVAL_TABLE"]).using("iceberg").partitionedBy("local_date").createOrReplace()
        return count
    finally:
        priced.unpersist()
        broadcast.unpersist()


def rebuild_aggregates(spark, args):
    from pyspark.sql import functions as F

    rows = spark.table(args["PRICED_INTERVAL_TABLE"]).filter(
        F.col("quality_status").isin("VALID", "VALID_WITH_WARNING"))
    products = (
        ("DAILY_SITE_TABLE", ("customer_id", "site_id", "local_date")),
        ("DAILY_CUSTOMER_TABLE", ("customer_id", "local_date")),
        ("MONTHLY_CUSTOMER_TABLE", ("customer_id", "local_month")),
    )
    rows = rows.withColumn("local_month", F.date_format("local_date", "yyyy-MM"))
    for argument, dimensions in products:
        product = rows.groupBy(*dimensions).agg(
            F.sum("consumption_kwh").cast("decimal(30,12)").alias("consumption_kwh"),
            F.sum("energy_cost_eur").cast("decimal(30,12)").alias("energy_cost_eur"))
        product.writeTo(args[argument]).using("iceberg").partitionedBy(dimensions[-1]).createOrReplace()
