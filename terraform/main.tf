provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = local.required_tags
  }
}

data "aws_caller_identity" "current" {}

locals {
  name_prefix     = "aws-lakehouse-platform-${var.demo_id}"
  operator_prefix = "aws-lakehouse-platform-demo"
  required_tags = {
    Project     = "aws-lakehouse-platform"
    Environment = "demo"
    ManagedBy   = "terraform"
    DemoId      = var.demo_id
    AutoCleanup = "true"
    ExpiresAt   = var.expires_at
  }
}

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "lakehouse" {
  bucket        = "${local.name_prefix}-${random_id.bucket_suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "lakehouse" {
  bucket                  = aws_s3_bucket.lakehouse.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lakehouse" {
  bucket = aws_s3_bucket.lakehouse.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_glue_catalog_database" "lakehouse" {
  name = replace(local.name_prefix, "-", "_")
}

output "landing_path" {
  value = "s3://${aws_s3_bucket.lakehouse.bucket}/landing/"
}

output "glue_database_name" {
  value = aws_glue_catalog_database.lakehouse.name
}
