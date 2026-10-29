"""Process one Bronze index batch into self-initializing Iceberg Silver tables."""

import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F
from pyspark.sql.window import Window


REGISTER_MAX_WH = 999_999_000
args = getResolvedOptions(sys.argv, ["JOB_NAME", "LANDING_PATH", "SILVER_INDEX_TABLE", "SILVER_INTERVAL_TABLE", "QUARANTINE_TABLE"])
glue_context = GlueContext(SparkContext.getOrCreate())
spark = glue_context.spark_session
job = Job(glue_context)
job.init(args["JOB_NAME"], args)

for statement in (
    f"""CREATE TABLE IF NOT EXISTS {args['SILVER_INDEX_TABLE']} (
      meter_id STRING, reading_at TIMESTAMP, meter_index_wh BIGINT, rollover_count INT,
      reading_context STRING, source_version INT, received_at TIMESTAMP, schema_version INT
    ) USING iceberg PARTITIONED BY (days(reading_at))""",
    f"""CREATE TABLE IF NOT EXISTS {args['SILVER_INTERVAL_TABLE']} (
      meter_id STRING, interval_start TIMESTAMP, interval_end TIMESTAMP,
      previous_index_wh BIGINT, meter_index_wh BIGINT, rollover_count INT,
      consumption_wh BIGINT, quality_status STRING
    ) USING iceberg PARTITIONED BY (days(interval_end))""",
    f"""CREATE TABLE IF NOT EXISTS {args['QUARANTINE_TABLE']} (
      meter_id STRING, reading_at TIMESTAMP, meter_index_wh BIGINT, rollover_count INT,
      reading_context STRING, source_version INT, received_at TIMESTAMP, schema_version INT,
      quarantine_reason STRING
    ) USING iceberg PARTITIONED BY (days(reading_at))""",
):
    spark.sql(statement)

landing = spark.read.option("recursiveFileLookup", "true").json(args["LANDING_PATH"]).select(
    F.col("meter_id").cast("string").alias("meter_id"),
    F.to_timestamp("reading_at").alias("reading_at"),
    F.col("meter_index_wh").cast("long").alias("meter_index_wh"),
    F.coalesce(F.col("rollover_count").cast("int"), F.lit(0)).alias("rollover_count"),
    F.col("reading_context").cast("string").alias("reading_context"),
    F.col("source_version").cast("int").alias("source_version"),
    F.to_timestamp("received_at").alias("received_at"),
    F.col("schema_version").cast("int").alias("schema_version"),
)
business_key = Window.partitionBy("meter_id", "reading_at").orderBy(F.col("source_version").desc(), F.col("received_at").desc())
latest = landing.withColumn("_version_rank", F.row_number().over(business_key)).filter("_version_rank = 1").drop("_version_rank")
invalid_condition = (
    F.col("meter_id").isNull() | F.col("reading_at").isNull() | F.col("meter_index_wh").isNull()
    | (F.col("meter_index_wh") < 0) | (F.col("rollover_count") < 0)
    | F.col("source_version").isNull() | (F.col("source_version") < 1)
    | F.col("received_at").isNull() | ~F.col("reading_context").isin("NORMAL", "INSTALLATION_BASELINE")
)
invalid = latest.filter(invalid_condition).withColumn("quarantine_reason", F.lit("CONTRACT_OR_VALUE_VIOLATION"))
valid = latest.filter(~invalid_condition)

valid.createOrReplaceTempView("incoming_indexes")
spark.sql(f"""
MERGE INTO {args['SILVER_INDEX_TABLE']} AS target USING incoming_indexes AS source
ON target.meter_id = source.meter_id AND target.reading_at = source.reading_at
WHEN MATCHED AND source.source_version > target.source_version THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
""")
invalid.createOrReplaceTempView("quarantined_indexes")
spark.sql(f"""
MERGE INTO {args['QUARANTINE_TABLE']} AS target USING quarantined_indexes AS source
ON target.meter_id <=> source.meter_id AND target.reading_at <=> source.reading_at
AND target.source_version = source.source_version AND target.quarantine_reason = source.quarantine_reason
WHEN NOT MATCHED THEN INSERT *
""")

affected = valid.select("meter_id").distinct()
sequence = Window.partitionBy("meter_id").orderBy("reading_at")
recalculated = (
    spark.table(args["SILVER_INDEX_TABLE"]).join(affected, "meter_id")
    .withColumn("previous_index_wh", F.lag("meter_index_wh").over(sequence))
    .withColumn("interval_start", F.lag("reading_at").over(sequence))
    .filter(F.col("previous_index_wh").isNotNull() & (F.col("reading_context") != "INSTALLATION_BASELINE"))
    .withColumn("consumption_wh", F.when(
        (F.col("meter_index_wh") < F.col("previous_index_wh")) & (F.col("rollover_count") == 0), F.lit(None).cast("long")
    ).when(F.col("rollover_count") > 0, F.lit(REGISTER_MAX_WH + 1) - F.col("previous_index_wh") + F.col("meter_index_wh"))
    .otherwise(F.col("meter_index_wh") - F.col("previous_index_wh")))
    .withColumn("interval_end", F.col("reading_at"))
    .withColumn("quality_status", F.when(F.col("consumption_wh").isNull(), F.lit("QUARANTINED")).otherwise(F.lit("VALID")))
    .select("meter_id", "interval_start", "interval_end", "previous_index_wh", "meter_index_wh", "rollover_count", "consumption_wh", "quality_status")
)
recalculated.createOrReplaceTempView("recalculated_intervals")
spark.sql(f"""
MERGE INTO {args['SILVER_INTERVAL_TABLE']} AS target USING recalculated_intervals AS source
ON target.meter_id = source.meter_id AND target.interval_end = source.interval_end
WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *
""")
job.commit()
