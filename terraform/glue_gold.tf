locals {
  gold_scripts = {
    price     = "jobs/gold_price_intervals.py"
    aggregate = "jobs/gold_aggregate_energy.py"
    pricing   = "src/lakehouse_platform/pricing.py"
    adapter   = "src/lakehouse_platform/gold_spark.py"
  }
  gold_jobs = {
    price = {
      script = "price"
      arguments = {
        "--INTERVAL_TABLE"        = "glue_catalog.${aws_glue_catalog_database.lakehouse.name}.meter_consumption_interval"
        "--SITE_METER_TABLE"      = "glue_catalog.${aws_glue_catalog_database.lakehouse.name}.site_meter_history"
        "--CUSTOMER_SITE_TABLE"   = "glue_catalog.${aws_glue_catalog_database.lakehouse.name}.customer_site_history"
        "--SITE_CONTRACT_TABLE"   = "glue_catalog.${aws_glue_catalog_database.lakehouse.name}.site_contract_history"
        "--TARIFF_TABLE"          = "glue_catalog.${aws_glue_catalog_database.lakehouse.name}.tariff_grid_history"
        "--PRICED_INTERVAL_TABLE" = "glue_catalog.${aws_glue_catalog_database.lakehouse.name}.priced_intervals"
      }
    }
    aggregate = {
      script = "aggregate"
      arguments = {
        "--PRICED_INTERVAL_TABLE"  = "glue_catalog.${aws_glue_catalog_database.lakehouse.name}.priced_intervals"
        "--DAILY_SITE_TABLE"       = "glue_catalog.${aws_glue_catalog_database.lakehouse.name}.daily_site_energy"
        "--DAILY_CUSTOMER_TABLE"   = "glue_catalog.${aws_glue_catalog_database.lakehouse.name}.daily_customer_energy"
        "--MONTHLY_CUSTOMER_TABLE" = "glue_catalog.${aws_glue_catalog_database.lakehouse.name}.monthly_customer_energy"
      }
    }
  }
}

resource "aws_s3_object" "gold_script" {
  for_each     = local.gold_scripts
  bucket       = aws_s3_bucket.lakehouse.id
  key          = "jobs/${basename(each.value)}"
  source       = "${path.module}/../${each.value}"
  etag         = filemd5("${path.module}/../${each.value}")
  content_type = "text/x-python"
  tags         = local.required_tags
}

resource "aws_glue_job" "gold" {
  for_each          = local.gold_jobs
  name              = "${local.name_prefix}-gold-${each.key}"
  role_arn          = aws_iam_role.glue_silver.arn
  glue_version      = "5.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = 30
  max_retries       = 0

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${aws_s3_bucket.lakehouse.id}/${aws_s3_object.gold_script[each.value.script].key}"
  }
  execution_property {
    max_concurrent_runs = 1
  }
  default_arguments = merge(aws_glue_job.silver_process_indexes.default_arguments, each.value.arguments, {
    "--extra-py-files"      = join(",", [for key in ["pricing", "adapter"] : "s3://${aws_s3_bucket.lakehouse.id}/${aws_s3_object.gold_script[key].key}"])
    "--job-bookmark-option" = "job-bookmark-disable"
  })
  tags = merge(local.required_tags, { Purpose = "GlueGold" })
}

output "gold_job_names" {
  value = { for key, job in aws_glue_job.gold : key => job.name }
}
