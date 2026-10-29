data "aws_iam_policy_document" "cleanup_build_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["codebuild.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cleanup_build" {
  name               = "aws-lakehouse-platform-demo-cleanup-build"
  assume_role_policy = data.aws_iam_policy_document.cleanup_build_assume_role.json
}

resource "aws_cloudwatch_log_group" "cleanup_build" {
  name              = "/aws/codebuild/aws-lakehouse-platform-demo-cleanup"
  retention_in_days = 14
}

data "aws_iam_policy_document" "cleanup_build" {
  statement {
    sid       = "WriteOnlyCleanupLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.cleanup_build.arn}:*"]
  }

  statement {
    sid       = "ReadAndWriteOnlyControlPlaneState"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.terraform_state.arn]
  }

  statement {
    sid     = "ReadAndWriteOnlyControlPlaneStateObjects"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [
      "${aws_s3_bucket.terraform_state.arn}/control-plane/terraform.tfstate",
      "${aws_s3_bucket.terraform_state.arn}/control-plane/terraform.tfstate.tflock"
    ]
  }

  statement {
    sid    = "DestroyOnlyProjectDataPlaneS3"
    effect = "Allow"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:DeleteBucket",
      "s3:DeleteBucket*",
      "s3:DeletePublicAccessBlock",
      "s3:DeleteObject",
      "s3:DeleteObjectVersion",
      "s3:Get*",
      "s3:ListBucket",
      "s3:ListBucketMultipartUploads",
      "s3:ListBucketVersions",
      "s3:ListMultipartUploadParts",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutEncryptionConfiguration"
    ]
    resources = [
      "arn:aws:s3:::aws-lakehouse-platform-*",
      "arn:aws:s3:::aws-lakehouse-platform-*/*"
    ]
  }

  statement {
    sid     = "DestroyOnlyProjectGlueDatabase"
    effect  = "Allow"
    actions = ["glue:DeleteDatabase", "glue:GetDatabase", "glue:GetTags"]
    resources = [
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/aws_lakehouse_platform_*",
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/aws_lakehouse_platform_*/*"
    ]
  }

}

resource "aws_iam_role_policy" "cleanup_build" {
  name   = "aws-lakehouse-platform-demo-cleanup-build"
  role   = aws_iam_role.cleanup_build.id
  policy = data.aws_iam_policy_document.cleanup_build.json
}

resource "aws_codebuild_project" "cleanup" {
  name           = "aws-lakehouse-platform-demo-cleanup"
  description    = "Verifies and destroys one approved aws-lakehouse-platform demonstration."
  build_timeout  = 10
  queued_timeout = 30
  service_role   = aws_iam_role.cleanup_build.arn

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type                = "BUILD_GENERAL1_SMALL"
    image                       = "aws/codebuild/standard:7.0"
    image_pull_credentials_type = "CODEBUILD"
    privileged_mode             = false
    type                        = "LINUX_CONTAINER"
  }

  logs_config {
    cloudwatch_logs {
      group_name  = aws_cloudwatch_log_group.cleanup_build.name
      stream_name = "cleanup"
      status      = "ENABLED"
    }
  }

  source {
    buildspec       = "scripts/cleanup/buildspec.yml"
    git_clone_depth = 1
    location        = "https://github.com/anthonyclavery/aws-lakehouse-platform.git"
    type            = "GITHUB"
  }

  source_version = "main"
}

data "aws_iam_policy_document" "cleanup_scheduler_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cleanup_scheduler" {
  name               = "aws-lakehouse-platform-demo-cleanup-scheduler"
  assume_role_policy = data.aws_iam_policy_document.cleanup_scheduler_assume_role.json
}

resource "aws_iam_role_policy" "cleanup_scheduler" {
  name = "aws-lakehouse-platform-demo-cleanup-scheduler"
  role = aws_iam_role.cleanup_scheduler.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["codebuild:StartBuild"], Resource = aws_codebuild_project.cleanup.arn }]
  })
}

resource "aws_scheduler_schedule" "demo_cleanup" {
  count                        = var.cleanup_schedule_enabled ? 1 : 0
  description                  = "Daily guarded destruction for the named aws-lakehouse-platform demonstration."
  name                         = "aws-lakehouse-platform-${var.cleanup_demo_id}-cleanup"
  schedule_expression          = "cron(0 20 * * ? *)"
  schedule_expression_timezone = "Europe/Paris"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_codebuild_project.cleanup.arn
    role_arn = aws_iam_role.cleanup_scheduler.arn
    input = jsonencode({
      environmentVariablesOverride = [
        { name = "PROJECT_NAME", value = "aws-lakehouse-platform", type = "PLAINTEXT" },
        { name = "DEMO_ID", value = var.cleanup_demo_id, type = "PLAINTEXT" },
        { name = "DEMO_EXPIRES_AT", value = var.cleanup_expires_at, type = "PLAINTEXT" },
        { name = "STATE_BUCKET", value = aws_s3_bucket.terraform_state.bucket, type = "PLAINTEXT" },
        { name = "AWS_REGION", value = var.aws_region, type = "PLAINTEXT" }
      ]
    })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 0
    }
  }

  lifecycle {
    precondition {
      condition = (
        can(regex("^[a-z0-9-]{3,32}$", var.cleanup_demo_id)) &&
        can(regex("^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.cleanup_expires_at))
      )
      error_message = "An enabled cleanup schedule requires a valid demo ID and UTC expiry timestamp."
    }
  }
}
