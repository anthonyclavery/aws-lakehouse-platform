"""Load one versioned Bronze reference flow into an Iceberg history table."""

import sys

def apply_reference_updates(spark, target, latest, keys):
    if not spark.catalog.tableExists(target):
        latest.writeTo(target).using("iceberg").create()
        return {"created": True}
    latest.createOrReplaceTempView("incoming_reference")
    join = " AND ".join(f"target.`{key}` = source.`{key}`" for key in keys)
    # A conditional MERGE can still rewrite unchanged files/snapshots. Gold
    # uses reference snapshots as its change contract, so do not issue a write
    # at all when no new key or higher source version is present.
    effective = spark.sql(f"""SELECT source.* FROM incoming_reference source LEFT JOIN {target} target
      ON {join} WHERE target.`{keys[0]}` IS NULL OR source.source_version > target.source_version""").cache()
    try:
        changed = effective.count()
        if changed:
            effective.createOrReplaceTempView("effective_reference")
            spark.sql(f"""MERGE INTO {target} AS target USING effective_reference AS source
              ON {join}
              WHEN MATCHED AND source.source_version > target.source_version THEN UPDATE SET *
              WHEN NOT MATCHED THEN INSERT *""")
        return {"created": False, "changed_reference_rows": changed}
    finally:
        effective.unpersist()


def main():
    import json
    from awsglue.context import GlueContext
    from awsglue.job import Job
    from awsglue.utils import getResolvedOptions
    from pyspark.context import SparkContext
    from pyspark.sql import functions as F
    from pyspark.sql.window import Window

    args = getResolvedOptions(sys.argv, ["JOB_NAME", "LANDING_PATH", "TARGET_TABLE", "BUSINESS_KEY_COLUMNS"])
    keys = [key.strip() for key in args["BUSINESS_KEY_COLUMNS"].split(",") if key.strip()]
    if not keys:
        raise ValueError("BUSINESS_KEY_COLUMNS must contain at least one column")
    context = GlueContext(SparkContext.getOrCreate())
    spark = context.spark_session
    job = Job(context)
    job.init(args["JOB_NAME"], args)
    incoming = spark.read.option("recursiveFileLookup", "true").json(args["LANDING_PATH"])
    latest = incoming.withColumn("_version_rank", F.row_number().over(
        Window.partitionBy(*keys).orderBy(F.col("source_version").desc(), F.col("received_at").desc())
    )).filter("_version_rank = 1").drop("_version_rank")
    print(json.dumps(apply_reference_updates(spark, args["TARGET_TABLE"], latest, keys), sort_keys=True), flush=True)
    job.commit()


if __name__ == "__main__":
    main()
