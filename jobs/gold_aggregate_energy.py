"""Rebuild daily site/customer and monthly customer Iceberg products."""

import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from gold_spark import rebuild_aggregates


def main():
    args = getResolvedOptions(sys.argv, [
        "JOB_NAME", "PRICED_INTERVAL_TABLE", "DAILY_SITE_TABLE",
        "DAILY_CUSTOMER_TABLE", "MONTHLY_CUSTOMER_TABLE"])
    context = GlueContext(SparkContext.getOrCreate())
    spark = context.spark_session
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.ansi.enabled", "true")
    job = Job(context)
    job.init(args["JOB_NAME"], args)
    rebuild_aggregates(spark, args)
    job.commit()


if __name__ == "__main__":
    main()
