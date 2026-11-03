variable "ingestion_access_enabled" {
  type        = bool
  default     = false
  description = "Enable an ephemeral, conditional-write-only Bronze ingestion identity."
}

variable "ingestion_governance_profile" {
  type        = string
  default     = null
  nullable    = true
  description = "Same-account administration profile for the ephemeral ingestion IAM resources."
}

provider "aws" {
  alias   = "ingestion_governance"
  region  = var.aws_region
  profile = var.ingestion_governance_profile
  default_tags { tags = local.required_tags }
}

data "aws_caller_identity" "ingestion_governance" {
  provider = aws.ingestion_governance
  count    = var.ingestion_access_enabled ? 1 : 0
}

resource "aws_iam_role" "ingestion" {
  provider             = aws.ingestion_governance
  count                = var.ingestion_access_enabled ? 1 : 0
  name                 = "${local.name_prefix}-ingestion"
  max_session_duration = 3600
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole",
  Principal = { AWS = aws_iam_role.terraform_operator.arn } }] })
  tags = local.required_tags
  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.ingestion_governance[0].account_id == data.aws_caller_identity.current.account_id
      error_message = "Ingestion governance and project identities must belong to the same AWS account."
    }
  }
}

resource "aws_iam_role_policy" "ingestion" {
  provider = aws.ingestion_governance
  count    = var.ingestion_access_enabled ? 1 : 0
  name     = "${local.name_prefix}-ingestion"
  role     = aws_iam_role.ingestion[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "LocateBucket", Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = aws_s3_bucket.lakehouse.arn },
    { Sid = "ListOnlyBatches", Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.lakehouse.arn,
    Condition = { StringLike = { "s3:prefix" = ["landing/batch_id=*/*"] } } },
    { Sid = "ReadOnlyBatches", Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.lakehouse.arn}/landing/batch_id=*/*" },
    { Sid = "CreateOnlyAbsentBatchObjects", Effect = "Allow", Action = ["s3:PutObject"], Resource = "${aws_s3_bucket.lakehouse.arn}/landing/batch_id=*/*",
    Condition = { StringEquals = { "s3:if-none-match" = "*" } } },
    { Sid = "DenyUnconditionalWrites", Effect = "Deny", Action = ["s3:PutObject"], Resource = "${aws_s3_bucket.lakehouse.arn}/landing/*",
    Condition = { StringNotEquals = { "s3:if-none-match" = "*" } } },
    { Sid = "DenyDeletes", Effect = "Deny", Action = ["s3:DeleteObject", "s3:DeleteObjectVersion"], Resource = "${aws_s3_bucket.lakehouse.arn}/*" },
    { Sid = "DenyOtherObjects", Effect = "Deny", Action = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"], NotResource = "${aws_s3_bucket.lakehouse.arn}/landing/batch_id=*/*" },
    { Sid = "DenyControlPlane", Effect = "Deny", Action = ["glue:*", "athena:*", "lakeformation:*", "iam:*"], Resource = "*" }
  ] })
}

output "ingestion_access" {
  value = var.ingestion_access_enabled ? {
    role_arn = aws_iam_role.ingestion[0].arn
  } : null
}
