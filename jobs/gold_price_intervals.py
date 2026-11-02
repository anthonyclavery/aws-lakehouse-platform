"""Rebuild priced Gold segments using the shared, tested tariff calculator."""

import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from gold_spark import rebuild_priced


def main():
    args = getResolvedOptions(sys.argv, [
        "JOB_NAME", "INTERVAL_TABLE", "SITE_METER_TABLE", "CUSTOMER_SITE_TABLE",
        "SITE_CONTRACT_TABLE", "TARIFF_TABLE", "PRICED_INTERVAL_TABLE"])
    context = GlueContext(SparkContext.getOrCreate())
    spark = context.spark_session
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.ansi.enabled", "true")
    job = Job(context)
    job.init(args["JOB_NAME"], args)
    print(f"Rebuilt {rebuild_priced(spark, args)} priced segments")
    job.commit()


if __name__ == "__main__":
    main()
