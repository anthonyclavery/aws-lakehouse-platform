"""Rebuild daily site/customer and monthly customer Iceberg products."""

import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from gold_refresh import aggregate


def main():
    args = getResolvedOptions(sys.argv, [
        "JOB_NAME", "BATCH_ID", "REFRESH_MODE", "INTERVAL_TABLE", "SITE_METER_TABLE", "CUSTOMER_SITE_TABLE",
        "SITE_CONTRACT_TABLE", "TARIFF_TABLE", "PRICED_INTERVAL_TABLE", "DAILY_SITE_TABLE",
        "DAILY_CUSTOMER_TABLE", "MONTHLY_CUSTOMER_TABLE"])
    context = GlueContext(SparkContext.getOrCreate())
    spark = context.spark_session
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.ansi.enabled", "true")
    job = Job(context)
    job.init(args["JOB_NAME"], args)
    print(aggregate(spark, args), flush=True)
    job.commit()


if __name__ == "__main__":
    main()
