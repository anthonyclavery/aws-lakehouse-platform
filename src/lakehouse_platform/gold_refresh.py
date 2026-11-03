"""Retryable Iceberg Gold refresh. Standalone module shipped to both Glue jobs.

One writer must own Silver, references and the entire Gold chain. Iceberg commits
are atomic per table; the durable stage makes the multi-table chain recoverable.
"""

import json
import re

REFERENCES = (("site_meter", "SITE_METER_TABLE"), ("customer_site", "CUSTOMER_SITE_TABLE"),
              ("site_contract", "SITE_CONTRACT_TABLE"), ("tariff_grid", "TARIFF_TABLE"))
PRODUCTS = (("DAILY_SITE_TABLE", ("customer_id", "site_id", "local_date")),
            ("DAILY_CUSTOMER_TABLE", ("customer_id", "local_date")),
            ("MONTHLY_CUSTOMER_TABLE", ("customer_id", "local_month")))
SEGMENT_KEY = ("meter_id", "interval_end", "segment_start", "segment_end")


def table_identity(args):
    return json.dumps({k: v for k, v in args.items() if k.endswith("_TABLE")}, sort_keys=True)


def validate(args):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", args["BATCH_ID"]):
        raise ValueError("Gold BATCH_ID must be an immutable lowercase run identifier")
    if args["REFRESH_MODE"] not in ("bounded", "full"):
        raise ValueError("REFRESH_MODE must be bounded or full")
    tables = [v for k, v in args.items() if k.endswith("_TABLE")]
    derived = [args["PRICED_INTERVAL_TABLE"] + suffix for suffix in ("_refresh", "_stage")]
    auxiliary = derived + [args["INTERVAL_TABLE"] + "_recalculation_work"]
    if (len(set(tables + auxiliary)) != len(tables + auxiliary) or any(
            not re.fullmatch(r"glue_catalog\.[a-z][a-z0-9_]{0,254}\.[a-z][a-z0-9_]{0,254}", t)
            for t in tables + auxiliary)):
        raise ValueError("Distinct simple Glue table identifiers required, including derived tables")
    return derived


def snapshot(spark, table):
    rows = spark.sql(f"SELECT snapshot_id FROM {table}.history ORDER BY made_current_at DESC LIMIT 1").collect()
    return str(rows[0][0]) if rows else ""


def at(spark, table, snapshot_id):
    if not snapshot_id:
        return spark.table(table).limit(0)
    return spark.read.format("iceberg").option("snapshot-id", snapshot_id).load(table)


def controls(spark, args):
    control, stage = validate(args)
    from gold_spark import SEGMENT_SCHEMA
    spark.sql(f"""CREATE TABLE IF NOT EXISTS {control} (
      batch_id STRING, generation BIGINT, mode STRING, state STRING,
      references_json STRING, interval_snapshot STRING, work_snapshot STRING, tables_json STRING
    ) USING iceberg""")
    spark.sql(f"""CREATE TABLE IF NOT EXISTS {stage} (
      {SEGMENT_SCHEMA}, batch_id STRING, kind STRING
    ) USING iceberg PARTITIONED BY (batch_id)""")
    return control, stage


def state(spark, control, batch):
    rows = spark.sql(f"SELECT * FROM {control} WHERE batch_id = '{batch}'").collect()
    return rows[0].asDict() if rows else None


def transition(spark, control, batch, target):
    spark.sql(f"UPDATE {control} SET state = '{target}' WHERE batch_id = '{batch}'")


def check_pending(spark, control, batch):
    if spark.sql(f"SELECT 1 FROM {control} WHERE state <> 'DONE' AND batch_id <> '{batch}' LIMIT 1").count():
        raise ValueError("Finish the pending Gold run before starting another run")


def start(spark, args, control):
    batch = args["BATCH_ID"]
    check_pending(spark, control, batch)
    existing = state(spark, control, batch)
    if existing:
        if existing["tables_json"] != table_identity(args):
            raise ValueError("Retry with the original source and destination tables")
        if existing["mode"] != args["REFRESH_MODE"]:
            raise ValueError("Retry with the original REFRESH_MODE")
        return existing
    from pyspark.sql import functions as F
    work = args["INTERVAL_TABLE"] + "_recalculation_work"
    if spark.table(work).filter(F.col("state") != "DONE").limit(1).count():
        raise ValueError("Finish Silver before starting Gold")
    refs = {key: snapshot(spark, args[key]) for _, key in REFERENCES}
    previous = spark.sql(f"SELECT * FROM {control} WHERE state = 'DONE' ORDER BY generation DESC LIMIT 1").collect()
    previous = previous[0].asDict() if previous else None
    if previous and previous["tables_json"] != table_identity(args):
        raise ValueError("Source or destination tables changed; migrate to a fresh destination")
    if previous and json.loads(previous["references_json"]) != refs and args["REFRESH_MODE"] != "full":
        raise ValueError("Reference snapshots changed; use a new full refresh run")
    if not previous and spark.catalog.tableExists(args["PRICED_INTERVAL_TABLE"]):
        raise ValueError("Existing Gold has no refresh checkpoint; migrate in a fresh destination database")
    row = (batch, (previous["generation"] + 1) if previous else 1, args["REFRESH_MODE"], "PREPARING",
           json.dumps(refs, sort_keys=True), snapshot(spark, args["INTERVAL_TABLE"]), snapshot(spark, work), table_identity(args))
    spark.createDataFrame([row], spark.table(control).schema).writeTo(control).append()
    return state(spark, control, batch)


def prepare(spark, args, control, stage, run):
    from pyspark.sql import functions as F
    from gold_spark import SEGMENT_SCHEMA, REFERENCE_LIMIT, price_partition, utc_record
    batch = args["BATCH_ID"]
    # PREPARING never publishes. A partial stage can therefore be discarded safely.
    spark.sql(f"DELETE FROM {stage} WHERE batch_id = '{batch}'")
    refs = {}
    for flow, key in REFERENCES:
        rows = at(spark, args[key], json.loads(run["references_json"])[key]).limit(REFERENCE_LIMIT + 1).collect()
        if len(rows) > REFERENCE_LIMIT:
            raise ValueError(f"{flow} exceeds demo broadcast limit {REFERENCE_LIMIT}")
        refs[flow] = [utc_record(row) for row in rows]
    intervals = at(spark, args["INTERVAL_TABLE"], run["interval_snapshot"])
    previous = spark.sql(f"""SELECT work_snapshot FROM {control} WHERE generation < {run['generation']}
      AND state = 'DONE' ORDER BY generation DESC LIMIT 1""").collect()
    old_exists = spark.catalog.tableExists(args["PRICED_INTERVAL_TABLE"])
    old = spark.table(args["PRICED_INTERVAL_TABLE"]) if old_exists else spark.createDataFrame([], SEGMENT_SCHEMA)
    if previous and run["mode"] == "bounded":
        work = args["INTERVAL_TABLE"] + "_recalculation_work"
        done = at(spark, work, run["work_snapshot"]).filter("state = 'DONE'")
        consumed = at(spark, work, previous[0][0]).filter("state = 'DONE'")
        keys = (done.join(consumed, ["batch_id", "meter_id", "reading_at"], "left_anti")
                .select("meter_id", F.col("reading_at").alias("interval_end")).distinct())
        intervals = intervals.join(keys, ["meter_id", "interval_end"])
        old = old.join(keys, ["meter_id", "interval_end"])
    broadcast = spark.sparkContext.broadcast(refs)
    fresh = spark.createDataFrame(intervals.rdd.mapPartitions(lambda rows: price_partition(rows, broadcast.value)),
                                  SEGMENT_SCHEMA).cache()
    try:
        count = fresh.count()  # Validate every tariff before recording a publishable stage.
        (old.select(*spark.createDataFrame([], SEGMENT_SCHEMA).columns).withColumn("kind", F.lit("OLD"))
         .unionByName(fresh.withColumn("kind", F.lit("NEW")))
         .withColumn("batch_id", F.lit(batch)).writeTo(stage).append())
        transition(spark, control, batch, "READY")
        return count
    finally:
        fresh.unpersist()
        broadcast.unpersist()


def merge_rows(spark, table, desired, keys, columns, deleted="_delete"):
    """Skip unchanged rows and missing tombstones, preserving replay snapshots."""
    desired.createOrReplaceTempView("gold_desired")
    join = " AND ".join(f"target.{k} <=> source.{k}" for k in keys)
    same = " AND ".join(f"target.{k} <=> source.{k}" for k in columns)
    changed = spark.sql(f"""SELECT source.* FROM gold_desired source LEFT JOIN {table} target ON {join}
      WHERE (source.{deleted} AND target.{keys[0]} IS NOT NULL)
      OR (NOT source.{deleted} AND (target.{keys[0]} IS NULL OR NOT ({same})))""").cache()
    try:
        count = changed.count()
        if count:
            changed.createOrReplaceTempView("gold_mutations")
            fields = ", ".join(columns)
            values = ", ".join(f"source.{k}" for k in columns)
            updates = ", ".join(f"{k} = source.{k}" for k in columns)
            spark.sql(f"""MERGE INTO {table} target USING gold_mutations source ON {join}
              WHEN MATCHED AND source.{deleted} THEN DELETE
              WHEN MATCHED THEN UPDATE SET {updates}
              WHEN NOT MATCHED AND NOT source.{deleted} THEN INSERT ({fields}) VALUES ({values})""")
        return count
    finally:
        changed.unpersist()


def price(spark, args):
    from pyspark.sql import functions as F
    from gold_spark import SEGMENT_COLUMNS, SEGMENT_SCHEMA
    control, stage = controls(spark, args)
    run = start(spark, args, control)
    if run["state"] in ("PRICED", "DONE"):
        return {"state": run["state"], "priced_mutations": 0}
    if run["state"] == "PREPARING":
        try:
            prepare(spark, args, control, stage, run)
        except Exception:
            # A business/Spark error before READY must not permanently pin bad
            # source data. Confirm the state: a READY commit may have succeeded
            # despite a lost response, in which case recovery must keep it.
            latest = state(spark, control, args["BATCH_ID"])
            if latest and latest["state"] == "PREPARING":
                spark.sql(f"DELETE FROM {stage} WHERE batch_id = '{args['BATCH_ID']}'")
                spark.sql(f"DELETE FROM {control} WHERE batch_id = '{args['BATCH_ID']}' AND state = 'PREPARING'")
            raise
    rows = spark.table(stage).filter(F.col("batch_id") == args["BATCH_ID"])
    fresh = rows.filter("kind = 'NEW'").select(*SEGMENT_COLUMNS)
    tombstones = (rows.filter("kind = 'OLD'").select(*SEGMENT_COLUMNS)
                  .join(fresh.select(*SEGMENT_KEY), list(SEGMENT_KEY), "left_anti"))
    desired = fresh.withColumn("_delete", F.lit(False)).unionByName(tombstones.withColumn("_delete", F.lit(True)))
    spark.sql(f"CREATE TABLE IF NOT EXISTS {args['PRICED_INTERVAL_TABLE']} ({SEGMENT_SCHEMA}) USING iceberg PARTITIONED BY (local_date)")
    count = merge_rows(spark, args["PRICED_INTERVAL_TABLE"], desired, SEGMENT_KEY, SEGMENT_COLUMNS)
    transition(spark, control, args["BATCH_ID"], "PRICED")
    return {"state": "PRICED", "priced_mutations": count}


def aggregate(spark, args):
    from pyspark.sql import functions as F
    control, stage = controls(spark, args)
    check_pending(spark, control, args["BATCH_ID"])
    run = state(spark, control, args["BATCH_ID"])
    if (not run or run["mode"] != args["REFRESH_MODE"] or run["state"] not in ("PRICED", "DONE")
            or run["tables_json"] != table_identity(args)):
        raise ValueError("Complete pricing with matching run arguments before aggregation")
    if run["state"] == "DONE":
        return {"state": "DONE", "aggregate_mutations": 0}
    touched = (spark.table(stage).filter(F.col("batch_id") == args["BATCH_ID"])
               .withColumn("local_month", F.date_format("local_date", "yyyy-MM")))
    rows = (spark.table(args["PRICED_INTERVAL_TABLE"]).filter("quality_status IN ('VALID', 'VALID_WITH_WARNING')")
            .withColumn("local_month", F.date_format("local_date", "yyyy-MM")))
    counts = {}
    for argument, dimensions in PRODUCTS:
        dims = list(dimensions)
        keys = touched.select(*dims).distinct()
        product = (rows.join(keys, dims).groupBy(*dims).agg(
            F.sum("consumption_kwh").cast("decimal(30,12)").alias("consumption_kwh"),
            F.sum("energy_cost_eur").cast("decimal(30,12)").alias("energy_cost_eur")))
        desired = keys.join(product, dims, "left").withColumn("_delete", F.col("consumption_kwh").isNull())
        schema = ", ".join(f"{k} {'date' if k == 'local_date' else 'string'}" for k in dims)
        spark.sql(f"""CREATE TABLE IF NOT EXISTS {args[argument]} ({schema}, consumption_kwh decimal(30,12),
          energy_cost_eur decimal(30,12)) USING iceberg PARTITIONED BY ({dims[-1]})""")
        counts[argument] = merge_rows(spark, args[argument], desired, dims,
                                      dims + ["consumption_kwh", "energy_cost_eur"])
    transition(spark, control, args["BATCH_ID"], "DONE")
    return {"state": "DONE", "aggregate_mutations": counts}
