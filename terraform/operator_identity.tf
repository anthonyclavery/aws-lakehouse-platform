resource "aws_iam_user" "operator" {
  name          = var.operator_iam_user_name
  force_destroy = false
  tags          = merge(local.required_tags, { Purpose = "LakehouseOperator" })

  lifecycle {
    prevent_destroy = true
  }
}

data "aws_iam_policy_document" "operator_assume_role" {
  statement {
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = [aws_iam_user.operator.arn]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "terraform_operator" {
  name               = "${local.operator_prefix}-terraform-operator"
  assume_role_policy = data.aws_iam_policy_document.operator_assume_role.json
  tags               = merge(local.required_tags, { Purpose = "LakehouseTerraformOperator" })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_user_policy" "operator_assume_project_role" {
  name = "${local.operator_prefix}-assume-terraform-operator"
  user = aws_iam_user.operator.name
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["sts:AssumeRole"], Resource = aws_iam_role.terraform_operator.arn }]
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_user_policy" "operator_self_manage_access_keys" {
  name = "${local.operator_prefix}-self-manage-access-keys"
  user = aws_iam_user.operator.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["iam:CreateAccessKey", "iam:DeleteAccessKey", "iam:ListAccessKeys", "iam:UpdateAccessKey"]
      Resource = aws_iam_user.operator.arn
    }]
  })

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_role_policy" "terraform_operator" {
  name = "${local.operator_prefix}-terraform"
  role = aws_iam_role.terraform_operator.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ManageOnlyProjectS3"
        Effect = "Allow"
        Action = [
          "s3:CreateBucket", "s3:DeleteBucket", "s3:Get*",
          "s3:ListBucket", "s3:PutBucket*", "s3:DeleteBucket*"
        ]
        Resource = ["arn:aws:s3:::aws-lakehouse-platform-*"]
      },
      {
        Sid    = "ManageOnlyProjectGlueDatabase"
        Effect = "Allow"
        Action = ["glue:CreateDatabase", "glue:DeleteDatabase", "glue:GetDatabase", "glue:GetTags", "glue:UpdateDatabase", "glue:TagResource", "glue:UntagResource"]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/aws_lakehouse_platform_*"
        ]
      },
      {
        Sid    = "ManageOnlyProjectGlueJobs"
        Effect = "Allow"
        Action = ["glue:CreateJob", "glue:DeleteJob", "glue:GetJob", "glue:GetTags", "glue:TagResource", "glue:UntagResource", "glue:UpdateJob"]
        Resource = [
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:job/aws-lakehouse-platform-*-silver-process-indexes",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:job/aws-lakehouse-platform-*-gold-price",
          "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:job/aws-lakehouse-platform-*-gold-aggregate"
        ]
      },
      {
        Sid      = "RunOnlyProjectGoldJobs"
        Effect   = "Allow"
        Action   = ["glue:StartJobRun", "glue:GetJobRun"]
        Resource = [for suffix in ["gold-price", "gold-aggregate"] : "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:job/aws-lakehouse-platform-*-${suffix}"]
      },
      {
        Sid      = "ManageOnlyProjectJobArtifact"
        Effect   = "Allow"
        Action   = ["s3:DeleteObject", "s3:GetObject", "s3:PutObject", "s3:GetObjectTagging", "s3:PutObjectTagging"]
        Resource = [for filename in ["silver_process_indexes.py", "gold_price_intervals.py", "gold_aggregate_energy.py", "pricing.py", "gold_spark.py"] : "arn:aws:s3:::aws-lakehouse-platform-*/jobs/${filename}"]
      },
      {
        Sid    = "ReadOnlyProjectIdentity"
        Effect = "Allow"
        Action = [
          "iam:GetRole", "iam:GetRolePolicy", "iam:GetUser", "iam:GetUserPolicy",
          "iam:ListAttachedRolePolicies", "iam:ListAttachedUserPolicies", "iam:ListRolePolicies", "iam:ListUserPolicies"
        ]
        Resource = [aws_iam_user.operator.arn, aws_iam_role.terraform_operator.arn]
      },
      {
        Sid    = "ManageOnlyProjectCleanupControlPlane"
        Effect = "Allow"
        Action = [
          "codebuild:BatchGetProjects", "codebuild:CreateProject", "codebuild:DeleteProject", "codebuild:GetResourcePolicy", "codebuild:TagResource", "codebuild:UntagResource", "codebuild:UpdateProject",
          "logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:PutRetentionPolicy", "logs:UntagResource",
          "scheduler:CreateSchedule", "scheduler:DeleteSchedule", "scheduler:GetSchedule", "scheduler:TagResource", "scheduler:UntagResource", "scheduler:UpdateSchedule",
          "iam:CreateRole", "iam:DeleteRole", "iam:DeleteRolePolicy", "iam:GetRole", "iam:GetRolePolicy", "iam:ListAttachedRolePolicies", "iam:ListRolePolicies", "iam:PassRole", "iam:PutRolePolicy", "iam:TagRole", "iam:UntagRole"
        ]
        Resource = [
          "arn:aws:codebuild:${var.aws_region}:${data.aws_caller_identity.current.account_id}:project/aws-lakehouse-platform-demo-cleanup",
          "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/codebuild/aws-lakehouse-platform-demo-cleanup",
          "arn:aws:scheduler:${var.aws_region}:${data.aws_caller_identity.current.account_id}:schedule/default/aws-lakehouse-platform-*-cleanup",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-lakehouse-platform-demo-cleanup-build",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-lakehouse-platform-demo-cleanup-scheduler",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-lakehouse-platform-demo-glue-silver"
        ]
      },
      {
        Sid      = "TagOnlyProjectCleanupLogGroup"
        Effect   = "Allow"
        Action   = ["logs:TagResource"]
        Resource = ["*"]
        Condition = {
          StringEquals = {
            "aws:RequestTag/Project"     = "aws-lakehouse-platform"
            "aws:RequestTag/Environment" = "control-plane"
            "aws:RequestTag/ManagedBy"   = "terraform"
            "aws:RequestTag/Purpose"     = "TerraformState"
            "aws:RequestTag/AutoCleanup" = "false"
          }
        }
      },
      {
        Sid      = "ReadOnlyLogGroupDiscovery"
        Effect   = "Allow"
        Action   = ["logs:DescribeLogGroups", "logs:ListTagsForResource"]
        Resource = ["*"]
      }
    ]
  })

  lifecycle {
    prevent_destroy = true
  }
}
