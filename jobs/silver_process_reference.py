"""Load one versioned Bronze reference flow into an Iceberg history table."""

import sys

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

glue_context = GlueContext(SparkContext.getOrCreate())
spark = glue_context.spark_session
job = Job(glue_context)
job.init(args["JOB_NAME"], args)

incoming = spark.read.option("recursiveFileLookup", "true").json(args["LANDING_PATH"])
latest = incoming.withColumn("_version_rank", F.row_number().over(
    Window.partitionBy(*keys).orderBy(F.col("source_version").desc(), F.col("received_at").desc())
)).filter("_version_rank = 1").drop("_version_rank")

if not spark.catalog.tableExists(args["TARGET_TABLE"]):
    latest.writeTo(args["TARGET_TABLE"]).using("iceberg").create()
else:
    latest.createOrReplaceTempView("incoming_reference")
    join = " AND ".join(f"target.`{key}` = source.`{key}`" for key in keys)
    spark.sql(f"""
    MERGE INTO {args['TARGET_TABLE']} AS target USING incoming_reference AS source
    ON {join}
    WHEN MATCHED AND source.source_version > target.source_version THEN UPDATE SET *
    WHEN NOT MATCHED THEN INSERT *
    """)
job.commit()
