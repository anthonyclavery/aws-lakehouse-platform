provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = "aws-lakehouse-platform"
      Environment = "control-plane"
      ManagedBy   = "terraform"
      Purpose     = "TerraformState"
      AutoCleanup = "false"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  bucket_name = "aws-lakehouse-platform-${data.aws_caller_identity.current.account_id}-${var.aws_region}-tfstate"
  state_keys = [
    "bootstrap-state/terraform.tfstate",
    "control-plane/terraform.tfstate"
  ]
}

resource "aws_s3_bucket" "terraform_state" {
  bucket        = local.bucket_name
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket                  = aws_s3_bucket.terraform_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_iam_policy_document" "terraform_state_access" {
  statement {
    sid       = "ListProjectTerraformStateBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.terraform_state.arn]
  }

  statement {
    sid    = "ReadAndWriteProjectTerraformState"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject"
    ]
    resources = flatten([
      for key in local.state_keys : [
        "${aws_s3_bucket.terraform_state.arn}/${key}",
        "${aws_s3_bucket.terraform_state.arn}/${key}.tflock"
      ]
    ])
  }
}

resource "aws_iam_role_policy" "terraform_state_access" {
  name   = "aws-lakehouse-platform-demo-terraform-state-backend"
  role   = var.terraform_operator_role_name
  policy = data.aws_iam_policy_document.terraform_state_access.json
}

output "terraform_state_bucket_name" {
  value = aws_s3_bucket.terraform_state.bucket
}
