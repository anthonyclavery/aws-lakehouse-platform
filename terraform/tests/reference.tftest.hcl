# Local plan only, with synthetic provider responses.
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
  demo_id    = "reference-local-test"
  expires_at = "2026-11-03T00:00:00Z"
}

run "reference_plan" {
  command = plan

  assert {
    condition = (
      aws_glue_job.silver_process_reference.name == "aws-lakehouse-platform-reference-local-test-silver-process-reference" &&
      aws_glue_job.silver_process_reference.glue_version == "5.0" &&
      aws_glue_job.silver_process_reference.worker_type == "G.1X" &&
      aws_glue_job.silver_process_reference.number_of_workers == 2 &&
      aws_glue_job.silver_process_reference.timeout == 30 &&
      aws_glue_job.silver_process_reference.max_retries == 0 &&
      aws_glue_job.silver_process_reference.execution_property[0].max_concurrent_runs == 1
    )
    error_message = "Reference job must remain explicitly invoked, bounded and sequential."
  }
  assert {
    condition = (
      aws_glue_job.silver_process_reference.default_arguments["--datalake-formats"] == "iceberg" &&
      aws_glue_job.silver_process_reference.default_arguments["--job-bookmark-option"] == "job-bookmark-disable" &&
      !contains(keys(aws_glue_job.silver_process_reference.default_arguments), "--LANDING_PATH")
    )
    error_message = "Reference runs need Iceberg and must require a flow-specific landing path."
  }
  assert {
    condition = (
      aws_s3_object.silver_process_reference_script.key == "jobs/silver_process_reference.py" &&
      aws_s3_object.silver_process_reference_script.etag == filemd5("../jobs/silver_process_reference.py") &&
      aws_s3_object.silver_process_reference_script.tags["DemoId"] == var.demo_id &&
      aws_glue_job.silver_process_reference.tags["AutoCleanup"] == "true"
    )
    error_message = "The deployed artifact must match the source and carry cleanup tags."
  }
}
