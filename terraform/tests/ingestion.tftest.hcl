mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "000000000000" } }
  mock_data "aws_iam_policy_document" { defaults = { json = "{}" } }
}
mock_provider "aws" { alias = "governance" }
mock_provider "aws" {
  alias = "ingestion_governance"
  mock_data "aws_caller_identity" { defaults = { account_id = "000000000000" } }
}
mock_provider "random" {}

variables {
  aws_region = "eu-west-3"
  demo_id    = "ingestion-test"
  expires_at = "2026-11-03T19:00:00Z"
}

run "ingestion_disabled_by_default" {
  command = plan
  assert {
    condition     = length(aws_iam_role.ingestion) == 0 && length(aws_iam_role_policy.ingestion) == 0
    error_message = "The dedicated ingestion identity must be explicitly enabled."
  }
}

run "ingestion_scoped_mock_apply" {
  command = apply
  variables { ingestion_access_enabled = true }
  override_resource {
    target = aws_iam_role.terraform_operator
    values = { arn = "arn:aws:iam::000000000000:role/aws-lakehouse-platform-demo-terraform-operator" }
  }
  override_resource {
    target = aws_iam_role.glue_silver
    values = { arn = "arn:aws:iam::000000000000:role/aws-lakehouse-platform-demo-glue-silver" }
  }
  override_resource {
    target = aws_s3_bucket.lakehouse
    values = { id = "aws-lakehouse-platform-ingestion-test-aabbccdd", arn = "arn:aws:s3:::aws-lakehouse-platform-ingestion-test-aabbccdd" }
  }
  assert {
    condition = (
      aws_iam_role.ingestion[0].max_session_duration == 3600 &&
      jsondecode(aws_iam_role.ingestion[0].assume_role_policy).Statement[0].Principal.AWS == aws_iam_role.terraform_operator.arn &&
      aws_iam_role.ingestion[0].tags["AutoCleanup"] == "true"
    )
    error_message = "Ingestion must trust only the project operator and retain cleanup tags."
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.ingestion[0].policy).Statement :
      s.Effect != "Allow" || alltrue([for a in s.Action : contains(["s3:GetBucketLocation", "s3:ListBucket", "s3:GetObject", "s3:PutObject"], a)])
    ])
    error_message = "Only location, scoped listing, batch reads and conditional creation may be allowed."
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.ingestion[0].policy).Statement :
      s.Condition.StringEquals["s3:if-none-match"] == "*" && endswith(s.Resource, "/landing/batch_id=*/*") if s.Sid == "CreateOnlyAbsentBatchObjects"
    ])
    error_message = "Creation must enforce If-None-Match:* on the exact Bronze batch prefix."
  }
  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.ingestion[0].policy).Statement :
      s.Condition.StringLike["s3:prefix"] == ["landing/batch_id=*/*"] if s.Sid == "ListOnlyBatches"
    ])
    error_message = "Bucket listing must stay inside batch prefixes."
  }
  assert {
    condition = length([for s in jsondecode(aws_iam_role_policy.ingestion[0].policy).Statement : s if
    s.Effect == "Deny" && contains(["DenyDeletes", "DenyOtherObjects", "DenyControlPlane", "DenyUnconditionalWrites"], s.Sid)]) == 4
    error_message = "Explicit deletion, non-Bronze, control-plane and unconditional-write denials are required."
  }
}

run "ingestion_rejects_cross_account_governance" {
  command = plan
  variables { ingestion_access_enabled = true }
  override_data {
    target = data.aws_caller_identity.ingestion_governance[0]
    values = { account_id = "111111111111" }
  }
  expect_failures = [aws_iam_role.ingestion[0]]
}
