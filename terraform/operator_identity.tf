resource "aws_iam_user" "operator" {
  name          = var.operator_iam_user_name
  force_destroy = true
  tags          = merge(local.required_tags, { Purpose = "LakehouseOperator" })
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
}

resource "aws_iam_user_policy" "operator_assume_project_role" {
  name = "${local.operator_prefix}-assume-terraform-operator"
  user = aws_iam_user.operator.name
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["sts:AssumeRole"], Resource = aws_iam_role.terraform_operator.arn }]
  })
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
          "codebuild:BatchGetProjects", "codebuild:CreateProject", "codebuild:DeleteProject", "codebuild:GetResourcePolicy", "codebuild:TagResource", "codebuild:UntagResource",
          "logs:CreateLogGroup", "logs:DeleteLogGroup", "logs:PutRetentionPolicy", "logs:UntagResource",
          "scheduler:CreateSchedule", "scheduler:DeleteSchedule", "scheduler:GetSchedule", "scheduler:TagResource", "scheduler:UntagResource",
          "iam:CreateRole", "iam:DeleteRole", "iam:DeleteRolePolicy", "iam:GetRole", "iam:GetRolePolicy", "iam:ListAttachedRolePolicies", "iam:ListRolePolicies", "iam:PassRole", "iam:PutRolePolicy", "iam:TagRole", "iam:UntagRole"
        ]
        Resource = [
          "arn:aws:codebuild:${var.aws_region}:${data.aws_caller_identity.current.account_id}:project/aws-lakehouse-platform-demo-cleanup",
          "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/codebuild/aws-lakehouse-platform-demo-cleanup",
          "arn:aws:scheduler:${var.aws_region}:${data.aws_caller_identity.current.account_id}:schedule/default/aws-lakehouse-platform-*-cleanup",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-lakehouse-platform-demo-cleanup-build",
          "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-lakehouse-platform-demo-cleanup-scheduler"
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
}
