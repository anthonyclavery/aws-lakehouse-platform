"""Apply one immutable batch and recalculate only changed/next interval keys.

A durable work table precedes the index MERGE. Retrying an interrupted batch
therefore completes its interval work even when every index already exists.
"""

import json
import re
import sys

REGISTER_MAX_WH = 999_999_000
INDEX_COLUMNS = ("meter_id", "reading_at", "meter_index_wh", "rollover_count", "reading_context",
                 "source_version", "received_at", "schema_version")
INTERVAL_COLUMNS = ("meter_id", "interval_start", "interval_end", "previous_index_wh", "meter_index_wh",
                    "rollover_count", "consumption_wh", "quality_status")


def validate_arguments(args):
    pattern = r"glue_catalog\.[a-z][a-z0-9_]{0,254}\.[a-z][a-z0-9_]{0,254}"
    tables = [args[key] for key in ("SILVER_INDEX_TABLE", "SILVER_INTERVAL_TABLE", "QUARANTINE_TABLE")]
    if any(not re.fullmatch(pattern, table) for table in tables) or len(set(tables)) != len(tables):
        raise ValueError("Distinct, simple glue_catalog database/table identifiers are required")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", args["BATCH_ID"]):
        raise ValueError("BATCH_ID must match an immutable lowercase landing batch identifier")
    work = args["SILVER_INTERVAL_TABLE"] + "_recalculation_work"
    if work in tables or len(work.rsplit(".", 1)[-1]) > 255:
        raise ValueError("The derived recalculation work table conflicts or exceeds the identifier limit")
    return work


def create_tables(spark, args, work):
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
        f"""CREATE TABLE IF NOT EXISTS {work} (
          batch_id STRING, meter_id STRING, reading_at TIMESTAMP, state STRING
        ) USING iceberg PARTITIONED BY (batch_id)""",
    ):
        spark.sql(statement)


def prepare_work(spark, args, work, valid):
    from pyspark.sql import functions as F
    from pyspark.sql.window import Window

    waiting = spark.table(work).filter(F.col("state") == "PENDING")
    if waiting.filter(F.col("batch_id") != args["BATCH_ID"]).limit(1).count():
        raise ValueError("Retry the pending batch before processing a different batch")
    stored = spark.table(args["SILVER_INDEX_TABLE"])
    effective = (valid.alias("incoming").join(stored.alias("stored"),
        (F.col("incoming.meter_id") == F.col("stored.meter_id")) &
        (F.col("incoming.reading_at") == F.col("stored.reading_at")), "left")
        .filter(F.col("stored.meter_id").isNull() | (F.col("incoming.source_version") > F.col("stored.source_version")))
        .select("incoming.*").cache())
    changed = effective.count()
    if changed:
        keys = effective.select("meter_id", "reading_at")
        projected = (stored.join(effective.select("meter_id").distinct(), "meter_id")
                     .join(keys, ["meter_id", "reading_at"], "left_anti").select(*INDEX_COLUMNS)
                     .unionByName(effective))
        sequence = Window.partitionBy("meter_id").orderBy("reading_at")
        neighbours = (projected.withColumn("next_reading_at", F.lead("reading_at").over(sequence))
                      .withColumn("previous_index_wh", F.lag("meter_index_wh").over(sequence)))
        changed_context = neighbours.join(keys, ["meter_id", "reading_at"])
        endpoints = keys.unionByName(changed_context.filter(F.col("next_reading_at").isNotNull())
                                   .select("meter_id", F.col("next_reading_at").alias("reading_at"))).distinct()
        contradictory = neighbours.join(endpoints, ["meter_id", "reading_at"]).filter(
            F.col("previous_index_wh").isNotNull() & (F.col("reading_context") != "INSTALLATION_BASELINE") & (
                ((F.col("meter_index_wh") < F.col("previous_index_wh")) & (F.col("rollover_count") == 0)) |
                ((F.col("meter_index_wh") >= F.col("previous_index_wh")) & (F.col("rollover_count") > 0)) |
                (F.col("previous_index_wh") > REGISTER_MAX_WH) | (F.col("meter_index_wh") > REGISTER_MAX_WH)))
        if contradictory.limit(1).count():
            effective.unpersist()
            raise ValueError("Batch contradicts an adjacent index sequence; accepted indexes and intervals were not changed")
        endpoints.withColumn("batch_id", F.lit(args["BATCH_ID"])).createOrReplaceTempView("new_interval_work")
        spark.sql(f"""MERGE INTO {work} target USING new_interval_work source
          ON target.batch_id = source.batch_id AND target.meter_id = source.meter_id
          AND target.reading_at = source.reading_at
          WHEN MATCHED THEN UPDATE SET state = 'PENDING'
          WHEN NOT MATCHED THEN INSERT (batch_id, meter_id, reading_at, state)
            VALUES (source.batch_id, source.meter_id, source.reading_at, 'PENDING')""")
    return effective, changed


def merge_indexes(spark, args, effective, changed):
    if changed:
        effective.createOrReplaceTempView("incoming_indexes")
        spark.sql(f"""MERGE INTO {args['SILVER_INDEX_TABLE']} target USING incoming_indexes source
          ON target.meter_id = source.meter_id AND target.reading_at = source.reading_at
          WHEN MATCHED AND source.source_version > target.source_version THEN UPDATE SET *
          WHEN NOT MATCHED THEN INSERT *""")


def apply_intervals(spark, args, work):
    from pyspark.sql import functions as F
    from pyspark.sql.window import Window

    keys = spark.table(work).filter((F.col("batch_id") == args["BATCH_ID"]) &
                                   (F.col("state") == "PENDING")).select("meter_id", "reading_at")
    pending = keys.count()
    if not pending:
        return 0, 0
    sequence = Window.partitionBy("meter_id").orderBy("reading_at")
    history = spark.table(args["SILVER_INDEX_TABLE"]).join(keys.select("meter_id").distinct(), "meter_id")
    context = (history.withColumn("previous_index_wh", F.lag("meter_index_wh").over(sequence))
               .withColumn("interval_start", F.lag("reading_at").over(sequence))
               .join(keys, ["meter_id", "reading_at"]))
    invalid = (((F.col("meter_index_wh") < F.col("previous_index_wh")) & (F.col("rollover_count") == 0)) |
               ((F.col("meter_index_wh") >= F.col("previous_index_wh")) & (F.col("rollover_count") > 0)) |
               (F.col("previous_index_wh") > REGISTER_MAX_WH))
    pairs = (context.withColumn("interval_end", F.col("reading_at"))
             .withColumn("_delete_interval", F.col("previous_index_wh").isNull() |
                         (F.col("reading_context") == "INSTALLATION_BASELINE"))
             .withColumn("consumption_wh", F.when(invalid, F.lit(None).cast("long")).otherwise(
                 F.col("meter_index_wh") - F.col("previous_index_wh") + F.col("rollover_count").cast("long") * F.lit(REGISTER_MAX_WH + 1)))
             .withColumn("quality_status", F.when(invalid, F.lit("QUARANTINED")).otherwise(F.lit("VALID")))
             .select(*INTERVAL_COLUMNS, "_delete_interval").cache())
    try:
        if pairs.count() != pending:
            raise ValueError("Pending work contains an endpoint missing from the committed index snapshot")
        pairs.createOrReplaceTempView("bounded_interval_pairs")
        same = " AND ".join(f"target.{field} <=> source.{field}" for field in INTERVAL_COLUMNS)
        mutations = spark.sql(f"""SELECT source.* FROM bounded_interval_pairs source
          LEFT JOIN {args['SILVER_INTERVAL_TABLE']} target
          ON target.meter_id = source.meter_id AND target.interval_end = source.interval_end
          WHERE (source._delete_interval AND target.meter_id IS NOT NULL)
          OR (NOT source._delete_interval AND (target.meter_id IS NULL OR NOT ({same})))""").cache()
        try:
            count = mutations.count()
            if count:
                mutations.createOrReplaceTempView("interval_mutations")
                fields = ", ".join(INTERVAL_COLUMNS)
                values = ", ".join(f"source.{field}" for field in INTERVAL_COLUMNS)
                updates = ", ".join(f"{field} = source.{field}" for field in INTERVAL_COLUMNS)
                spark.sql(f"""MERGE INTO {args['SILVER_INTERVAL_TABLE']} target USING interval_mutations source
                  ON target.meter_id = source.meter_id AND target.interval_end = source.interval_end
                  WHEN MATCHED AND source._delete_interval THEN DELETE
                  WHEN MATCHED THEN UPDATE SET {updates}
                  WHEN NOT MATCHED AND NOT source._delete_interval THEN INSERT ({fields}) VALUES ({values})""")
            return pending, count
        finally:
            mutations.unpersist()
    finally:
        pairs.unpersist()


def finish_work(spark, args, work):
    from pyspark.sql import functions as F

    spark.table(work).filter((F.col("batch_id") == args["BATCH_ID"]) & (F.col("state") == "PENDING"))\
        .select("batch_id", "meter_id", "reading_at").createOrReplaceTempView("completed_interval_work")
    spark.sql(f"""MERGE INTO {work} target USING completed_interval_work source
      ON target.batch_id = source.batch_id AND target.meter_id = source.meter_id AND target.reading_at = source.reading_at
      WHEN MATCHED THEN UPDATE SET state = 'DONE'""")


def run_batch(spark, args, work, valid):
    effective, changed = prepare_work(spark, args, work, valid)
    try:
        merge_indexes(spark, args, effective, changed)
        pending, mutations = apply_intervals(spark, args, work)
        if pending:
            finish_work(spark, args, work)
        return {"batch_id":args["BATCH_ID"], "changed_indexes":changed,
                "recalculated_interval_keys":pending, "interval_mutations":mutations}
    finally:
        effective.unpersist()


def main():
    from awsglue.context import GlueContext
    from awsglue.job import Job
    from awsglue.utils import getResolvedOptions
    from pyspark.context import SparkContext
    from pyspark.sql import functions as F
    from pyspark.sql.window import Window

    args = getResolvedOptions(sys.argv, ["JOB_NAME", "BATCH_ID", "LANDING_PATH", "SILVER_INDEX_TABLE",
                                        "SILVER_INTERVAL_TABLE", "QUARANTINE_TABLE"])
    work = validate_arguments(args)
    context = GlueContext(SparkContext.getOrCreate())
    spark = context.spark_session
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    job = Job(context)
    job.init(args["JOB_NAME"], args)
    create_tables(spark, args, work)
    landing = spark.read.option("recursiveFileLookup", "true").json(args["LANDING_PATH"]).select(
        F.col("meter_id").cast("string").alias("meter_id"), F.to_timestamp("reading_at").alias("reading_at"),
        F.col("meter_index_wh").cast("long").alias("meter_index_wh"),
        F.when(F.col("rollover_count").isNull(), F.lit(0)).otherwise(F.col("rollover_count").cast("int")).alias("rollover_count"),
        F.col("reading_context").cast("string").alias("reading_context"),
        F.col("source_version").cast("int").alias("source_version"), F.to_timestamp("received_at").alias("received_at"),
        F.col("schema_version").cast("int").alias("schema_version"))
    ordering = Window.partitionBy("meter_id", "reading_at").orderBy(F.col("source_version").desc(), F.col("received_at").desc())
    latest = landing.withColumn("_rank", F.row_number().over(ordering)).filter("_rank = 1").drop("_rank")
    bad = F.coalesce(F.col("meter_id").isNull() | (F.col("meter_id") == "") | F.col("reading_at").isNull() |
        F.col("meter_index_wh").isNull() | (F.col("meter_index_wh") < 0) | (F.col("meter_index_wh") > REGISTER_MAX_WH) |
        F.col("rollover_count").isNull() | (F.col("rollover_count") < 0) | F.col("source_version").isNull() | (F.col("source_version") < 1) |
        F.col("received_at").isNull() | (F.col("schema_version") != 1) | F.col("schema_version").isNull() |
        ~F.col("reading_context").isin("NORMAL", "INSTALLATION_BASELINE"), F.lit(True))
    invalid = latest.filter(bad).withColumn("quarantine_reason", F.lit("CONTRACT_OR_VALUE_VIOLATION"))
    if invalid.limit(1).count():
        invalid.createOrReplaceTempView("quarantined_indexes")
        spark.sql(f"""MERGE INTO {args['QUARANTINE_TABLE']} target USING quarantined_indexes source
          ON target.meter_id <=> source.meter_id AND target.reading_at <=> source.reading_at
          AND target.source_version <=> source.source_version AND target.quarantine_reason = source.quarantine_reason
          WHEN NOT MATCHED THEN INSERT *""")
    print(json.dumps(run_batch(spark, args, work, latest.filter(~bad)), sort_keys=True), flush=True)
    job.commit()


if __name__ == "__main__":
    main()
