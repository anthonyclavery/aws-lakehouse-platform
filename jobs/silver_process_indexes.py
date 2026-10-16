"""AWS Glue PySpark job: landing consumption indexes to Iceberg Silver.

Terraform will provide Glue, S3, Iceberg and Lake Formation configuration in a
later phase. This source is not executed locally.
"""

import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F
from pyspark.sql.window import Window


args = getResolvedOptions(sys.argv, ["JOB_NAME", "LANDING_PATH", "SILVER_INDEX_TABLE", "SILVER_INTERVAL_TABLE", "QUARANTINE_TABLE"])
glue_context = GlueContext(SparkContext.getOrCreate())
spark = glue_context.spark_session
job = Job(glue_context)
job.init(args["JOB_NAME"], args)

landing = spark.read.json(args["LANDING_PATH"])
business_key = Window.partitionBy("meter_id", "reading_at").orderBy(F.col("source_version").desc(), F.col("received_at").desc())
latest = landing.withColumn("_version_rank", F.row_number().over(business_key)).filter("_version_rank = 1").drop("_version_rank")
invalid = latest.filter(F.col("meter_id").isNull() | F.col("reading_at").isNull() | F.col("meter_index_wh").isNull() | (F.col("meter_index_wh") < 0) | (F.col("source_version") < 1)).withColumn("quarantine_reason", F.lit("CONTRACT_OR_VALUE_VIOLATION"))
valid = latest.join(invalid.select("meter_id", "reading_at"), ["meter_id", "reading_at"], "left_anti")

valid.createOrReplaceTempView("incoming_indexes")
spark.sql(f"""
MERGE INTO {args['SILVER_INDEX_TABLE']} AS target USING incoming_indexes AS source
ON target.meter_id = source.meter_id AND target.reading_at = source.reading_at
WHEN MATCHED AND source.source_version > target.source_version THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
""")
invalid.createOrReplaceTempView("quarantined_indexes")
spark.sql(f"INSERT INTO {args['QUARANTINE_TABLE']} SELECT * FROM quarantined_indexes")

affected = valid.select("meter_id").distinct()
sequence = Window.partitionBy("meter_id").orderBy("reading_at")
recalculated = spark.table(args["SILVER_INDEX_TABLE"]).join(affected, "meter_id").withColumn("previous_index_wh", F.lag("meter_index_wh").over(sequence)).withColumn("interval_start", F.lag("reading_at").over(sequence)).filter("previous_index_wh IS NOT NULL").withColumn("consumption_wh", F.when((F.col("meter_index_wh") < F.col("previous_index_wh")) & (F.col("rollover_count") == 0), F.lit(None)).when(F.col("rollover_count") > 0, (F.lit(999999001) - F.col("previous_index_wh")) + F.col("meter_index_wh")).otherwise(F.col("meter_index_wh") - F.col("previous_index_wh"))).withColumn("quality_status", F.when(F.col("consumption_wh").isNull(), F.lit("QUARANTINED")).otherwise(F.lit("VALID")))
recalculated.createOrReplaceTempView("recalculated_intervals")
spark.sql(f"""
MERGE INTO {args['SILVER_INTERVAL_TABLE']} AS target USING recalculated_intervals AS source
ON target.meter_id = source.meter_id AND target.interval_end = source.reading_at
WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *
""")
job.commit()
