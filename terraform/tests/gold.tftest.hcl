# Synthetic provider responses only: this test never contacts AWS.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "000000000000" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{}" }
  }
}
mock_provider "random" {}
mock_provider "aws" { alias = "governance" }

variables {
  aws_region = "eu-west-3"
  demo_id    = "gold-local-test"
  expires_at = "2026-11-03T00:00:00Z"
}

run "gold_plan" {
  command = plan

  assert {
    condition     = length(aws_glue_job.gold) == 2 && length(aws_s3_object.gold_script) == 5
    error_message = "Gold requires two jobs and all five script/module artifacts."
  }
  assert {
    condition = alltrue([for job in aws_glue_job.gold :
      job.glue_version == "5.0" && job.worker_type == "G.1X" && job.number_of_workers == 2 &&
      job.timeout == 30 && job.max_retries == 0 && job.execution_property[0].max_concurrent_runs == 1
    ])
    error_message = "Gold job cost and concurrency bounds changed."
  }
  assert {
    condition = alltrue([for job in aws_glue_job.gold :
      job.default_arguments["--job-bookmark-option"] == "job-bookmark-disable" &&
      job.default_arguments["--datalake-formats"] == "iceberg" &&
      contains(keys(job.default_arguments), "--extra-py-files") &&
      contains(keys(job.default_arguments), "--INTERVAL_TABLE") &&
      contains(keys(job.default_arguments), "--MONTHLY_CUSTOMER_TABLE")
    ])
    error_message = "Gold must load Iceberg/shared modules and use explicit refresh checkpoints."
  }
  assert {
    condition     = local.gold_scripts.refresh == "src/lakehouse_platform/gold_refresh.py"
    error_message = "Gold must ship the shared durable refresh module."
  }
  assert {
    condition = (
      aws_glue_job.gold["price"].default_arguments["--PRICED_INTERVAL_TABLE"] ==
      aws_glue_job.gold["aggregate"].default_arguments["--PRICED_INTERVAL_TABLE"] &&
      endswith(aws_glue_job.gold["aggregate"].default_arguments["--DAILY_SITE_TABLE"], ".daily_site_energy") &&
      endswith(aws_glue_job.gold["aggregate"].default_arguments["--MONTHLY_CUSTOMER_TABLE"], ".monthly_customer_energy")
    )
    error_message = "The Gold chain must share a priced snapshot and expose every product."
  }
  assert {
    condition = alltrue([for artifact in aws_s3_object.gold_script :
      artifact.tags["DemoId"] == var.demo_id && artifact.tags["AutoCleanup"] == "true" && artifact.tags["ExpiresAt"] == var.expires_at
    ])
    error_message = "All Gold objects require tags accepted by guarded cleanup."
  }
}
