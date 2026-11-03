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
  demo_id    = "benchmark-local-test"
  expires_at = "2026-11-03T19:00:00Z"
}

run "partition_benchmark_plan" {
  command = plan
  assert {
    condition     = length(aws_s3_object.benchmark_script) == 2 && length(aws_glue_job.gold) == 2
    error_message = "Benchmark ships two artifacts and reuses the bounded existing Glue runtime."
  }
  assert {
    condition = alltrue([for object in aws_s3_object.benchmark_script :
      object.tags["DemoId"] == var.demo_id && object.tags["AutoCleanup"] == "true" && object.tags["ExpiresAt"] == var.expires_at
    ])
    error_message = "Every benchmark artifact requires guarded cleanup tags."
  }
}
