# All providers below are mocked. This file never contacts AWS.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "000000000000" }
  }
  mock_data "aws_iam_policy_document" { defaults = { json = "{}" } }
}
mock_provider "aws" {
  alias = "governance"
  mock_data "aws_caller_identity" {
    defaults = { account_id = "000000000000", arn = "arn:aws:iam::000000000000:user/governance" }
  }
}
mock_provider "random" {}

variables {
  aws_region = "eu-west-3"
  demo_id    = "analyst-local-test"
  expires_at = "2026-11-03T19:00:00Z"
}

run "analyst_disabled_by_default" {
  command = plan
  assert {
    condition     = length(aws_iam_role.analyst) == 0 && length(aws_lakeformation_resource.gold) == 0
    error_message = "Analyst governance must not be enabled before Gold exists."
  }
}

run "analyst_scoped_mock_apply" {
  command = apply
  variables { analyst_access_enabled = true }
  override_resource {
    target = aws_s3_bucket.lakehouse
    values = { id = "aws-lakehouse-platform-analyst-local-test-aabbccdd", arn = "arn:aws:s3:::aws-lakehouse-platform-analyst-local-test-aabbccdd" }
  }
  override_resource {
    target = aws_glue_catalog_database.lakehouse
    values = { arn = "arn:aws:glue:eu-west-3:000000000000:database/aws_lakehouse_platform_analyst_local_test" }
  }
  override_resource {
    target = aws_iam_role.terraform_operator
    values = { arn = "arn:aws:iam::000000000000:role/aws-lakehouse-platform-demo-terraform-operator" }
  }
  override_resource {
    target = aws_iam_role.glue_silver
    values = { arn = "arn:aws:iam::000000000000:role/aws-lakehouse-platform-demo-glue-silver" }
  }
  override_resource {
    target = aws_iam_role.analyst[0]
    values = { arn = "arn:aws:iam::000000000000:role/aws-lakehouse-platform-analyst-local-test-analyst" }
  }
  override_resource {
    target = aws_iam_role.lakeformation_data[0]
    values = { arn = "arn:aws:iam::000000000000:role/aws-lakehouse-platform-analyst-local-test-lakeformation-data" }
  }
  override_resource {
    target = aws_athena_workgroup.analyst[0]
    values = { arn = "arn:aws:athena:eu-west-3:000000000000:workgroup/aws-lakehouse-platform-analyst-local-test-analyst" }
  }
  override_data {
    target = data.aws_glue_catalog_table.analyst_gold["daily_site_energy"]
    values = { storage_descriptor = [{ location = "s3://aws-lakehouse-platform-analyst-local-test-aabbccdd/warehouse/aws_lakehouse_platform_analyst_local_test.db/daily_site_energy" }] }
  }
  override_data {
    target = data.aws_glue_catalog_table.analyst_gold["daily_customer_energy"]
    values = { storage_descriptor = [{ location = "s3://aws-lakehouse-platform-analyst-local-test-aabbccdd/warehouse/aws_lakehouse_platform_analyst_local_test.db/daily_customer_energy" }] }
  }
  override_data {
    target = data.aws_glue_catalog_table.analyst_gold["monthly_customer_energy"]
    values = { storage_descriptor = [{ location = "s3://aws-lakehouse-platform-analyst-local-test-aabbccdd/warehouse/aws_lakehouse_platform_analyst_local_test.db/monthly_customer_energy" }] }
  }
  assert {
    condition = length(aws_lakeformation_resource.gold) == 3 && alltrue([
      for grant in aws_lakeformation_permissions.analyst_gold :
      grant.permissions == toset(["SELECT", "DESCRIBE"]) && grant.table[0].wildcard != true
    ])
    error_message = "Analysts may read only three exact Gold resources."
  }
  assert {
    condition = (
      aws_athena_workgroup.analyst[0].configuration[0].enforce_workgroup_configuration &&
      aws_athena_workgroup.analyst[0].configuration[0].bytes_scanned_cutoff_per_query == 104857600 &&
      endswith(aws_athena_workgroup.analyst[0].configuration[0].result_configuration[0].output_location, "/athena-results/analyst/")
    )
    error_message = "Analysts require isolated encrypted result storage and a bounded workgroup."
  }
  assert {
    condition = length([for statement in jsondecode(aws_iam_role_policy.analyst[0].policy).Statement : statement
      if statement.Effect == "Deny" && contains(statement.Action, "s3:GetObject")]) == 1
    error_message = "The analyst must be explicitly denied direct warehouse/landing reads."
  }
  assert {
    condition = alltrue([for statement in jsondecode(aws_iam_role_policy.lakeformation_data[0].policy).Statement :
      !contains(statement.Action, "s3:PutObject") && !contains(statement.Action, "s3:DeleteObject")
    ])
    error_message = "LF credential vending must remain read-only."
  }
  assert {
    condition     = aws_iam_role.analyst[0].tags["DemoId"] == var.demo_id && aws_iam_role.lakeformation_data[0].tags["AutoCleanup"] == "true"
    error_message = "Both temporary identities must carry guarded cleanup tags."
  }
}
