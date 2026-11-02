mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "000000000000" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{}" }
  }
}
mock_provider "random" {}

variables {
  aws_region = "eu-west-3"
  demo_id    = "athena-local-test"
  expires_at = "2026-11-03T00:00:00Z"
}

run "athena_plan" {
  command = plan
  assert {
    condition = (
      aws_athena_workgroup.demo.configuration[0].enforce_workgroup_configuration &&
      aws_athena_workgroup.demo.configuration[0].bytes_scanned_cutoff_per_query == 1073741824 &&
      aws_athena_workgroup.demo.configuration[0].engine_version[0].selected_engine_version == "Athena engine version 3" &&
      aws_athena_workgroup.demo.configuration[0].result_configuration[0].encryption_configuration[0].encryption_option == "SSE_S3" &&
      aws_athena_workgroup.demo.tags["DemoId"] == var.demo_id &&
      aws_athena_workgroup.demo.tags["AutoCleanup"] == "true"
    )
    error_message = "Qualification queries require bounded scans, encryption and guarded cleanup tags."
  }
}
