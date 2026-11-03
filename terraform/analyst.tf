variable "analyst_access_enabled" {
  type        = bool
  description = "Enable only after canonical Gold tables exist; then seal their legacy IAM grants."
  default     = false
}

variable "analyst_governance_profile" {
  type        = string
  default     = null
  nullable    = true
  description = "Administrative profile for explicit project-scoped LF setup; never an analyst profile."
}

provider "aws" {
  alias   = "governance"
  region  = var.aws_region
  profile = var.analyst_governance_profile
  default_tags { tags = local.required_tags }
}

data "aws_caller_identity" "governance" {
  provider = aws.governance
  count    = var.analyst_access_enabled ? 1 : 0
}

locals {
  analyst_gold      = var.analyst_access_enabled ? toset(["daily_site_energy", "daily_customer_energy", "monthly_customer_energy"]) : toset([])
  analyst_role_name = "${local.name_prefix}-analyst"
  lf_role_name      = "${local.name_prefix}-lakeformation-data"
  cleanup_role_arn  = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-lakehouse-platform-demo-cleanup-build"
  gold_locations    = { for name, table in data.aws_glue_catalog_table.analyst_gold : name => trimsuffix(table.storage_descriptor[0].location, "/") }
  gold_keys         = { for name, location in local.gold_locations : name => trimprefix(location, "s3://${aws_s3_bucket.lakehouse.id}/") }
}

data "aws_glue_catalog_table" "analyst_gold" {
  provider      = aws.governance
  for_each      = local.analyst_gold
  database_name = aws_glue_catalog_database.lakehouse.name
  name          = each.value
}

resource "aws_iam_role" "analyst" {
  provider             = aws.governance
  count                = var.analyst_access_enabled ? 1 : 0
  name                 = local.analyst_role_name
  max_session_duration = 3600
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole",
  Principal = { AWS = aws_iam_role.terraform_operator.arn } }] })
  tags = local.required_tags
  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.governance[0].account_id == data.aws_caller_identity.current.account_id
      error_message = "Governance and project profiles must use the same AWS account."
    }
  }
}

resource "aws_athena_workgroup" "analyst" {
  provider      = aws.governance
  count         = var.analyst_access_enabled ? 1 : 0
  name          = "${local.name_prefix}-analyst"
  force_destroy = true
  configuration {
    enforce_workgroup_configuration    = true
    bytes_scanned_cutoff_per_query     = 104857600
    publish_cloudwatch_metrics_enabled = true
    engine_version { selected_engine_version = "Athena engine version 3" }
    result_configuration {
      output_location = "s3://${aws_s3_bucket.lakehouse.id}/athena-results/analyst/"
      encryption_configuration { encryption_option = "SSE_S3" }
    }
  }
  tags = local.required_tags
}

resource "aws_iam_role_policy" "analyst" {
  provider = aws.governance
  count    = var.analyst_access_enabled ? 1 : 0
  name     = local.analyst_role_name
  role     = aws_iam_role.analyst[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:StopQueryExecution", "athena:GetWorkGroup"], Resource = aws_athena_workgroup.analyst[0].arn },
    { Effect = "Allow", Action = ["glue:GetDatabase"], Resource = ["arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog", aws_glue_catalog_database.lakehouse.arn] },
    { Effect = "Allow", Action = ["glue:GetTable", "glue:GetPartition", "glue:GetPartitions", "glue:BatchGetPartition"], Resource = concat([
      "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog", aws_glue_catalog_database.lakehouse.arn],
    [for name in local.analyst_gold : "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${aws_glue_catalog_database.lakehouse.name}/${name}"]) },
    { Effect = "Allow", Action = ["lakeformation:GetDataAccess"], Resource = "*" },
    { Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = aws_s3_bucket.lakehouse.arn },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.lakehouse.arn, Condition = { StringLike = { "s3:prefix" = ["athena-results/analyst/", "athena-results/analyst/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"], Resource = "${aws_s3_bucket.lakehouse.arn}/athena-results/analyst/*" },
    { Effect = "Deny", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${aws_s3_bucket.lakehouse.arn}/landing/*", "${aws_s3_bucket.lakehouse.arn}/warehouse/*"] }
  ] })
}

resource "aws_iam_role" "lakeformation_data" {
  provider = aws.governance
  count    = var.analyst_access_enabled ? 1 : 0
  name     = local.lf_role_name
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole",
  Principal = { Service = "lakeformation.amazonaws.com" }, Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } } }] })
  tags = local.required_tags
}

resource "aws_iam_role_policy" "lakeformation_data" {
  provider = aws.governance
  count    = var.analyst_access_enabled ? 1 : 0
  name     = local.lf_role_name
  role     = aws_iam_role.lakeformation_data[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = aws_s3_bucket.lakehouse.arn },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.lakehouse.arn,
    Condition = { StringLike = { "s3:prefix" = flatten([for key in values(local.gold_keys) : [key, "${key}/*"]]) } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = [for location in values(local.gold_locations) : "${replace(location, "s3://", "arn:aws:s3:::")}/*"] }
  ] })
}

resource "aws_lakeformation_resource" "gold" {
  provider   = aws.governance
  for_each   = local.analyst_gold
  arn        = replace(local.gold_locations[each.key], "s3://", "arn:aws:s3:::")
  role_arn   = aws_iam_role.lakeformation_data[0].arn
  depends_on = [aws_iam_role_policy.lakeformation_data]
  lifecycle {
    precondition {
      condition = contains([
        "s3://${aws_s3_bucket.lakehouse.id}/warehouse/${aws_glue_catalog_database.lakehouse.name}/${each.key}",
        "s3://${aws_s3_bucket.lakehouse.id}/warehouse/${aws_glue_catalog_database.lakehouse.name}.db/${each.key}"
      ], local.gold_locations[each.key])
      error_message = "Register only the exact canonical Gold table location inside this project warehouse."
    }
  }
}

# Cleanup receives grantable rights only on these exact demo resources. The
# dependency order retains those rights until all other grants have been revoked.
resource "aws_lakeformation_permissions" "cleanup_gold" {
  provider                      = aws.governance
  for_each                      = local.analyst_gold
  principal                     = local.cleanup_role_arn
  permissions                   = ["ALL"]
  permissions_with_grant_option = ["ALL"]
  table {
    database_name = aws_glue_catalog_database.lakehouse.name
    name          = each.key
  }
}

resource "aws_lakeformation_permissions" "governance_gold" {
  provider                      = aws.governance
  for_each                      = local.analyst_gold
  principal                     = data.aws_caller_identity.governance[0].arn
  permissions                   = ["ALL"]
  permissions_with_grant_option = ["ALL"]
  table {
    database_name = aws_glue_catalog_database.lakehouse.name
    name          = each.key
  }
  depends_on = [aws_lakeformation_permissions.cleanup_gold]
}

resource "aws_lakeformation_permissions" "writer_gold" {
  provider                      = aws.governance
  for_each                      = local.analyst_gold
  principal                     = aws_iam_role.glue_silver.arn
  permissions                   = ["ALL", "ALTER", "DELETE", "DESCRIBE", "DROP", "INSERT", "SELECT"]
  permissions_with_grant_option = ["ALL", "ALTER", "DELETE", "DESCRIBE", "DROP", "INSERT", "SELECT"]
  table {
    database_name = aws_glue_catalog_database.lakehouse.name
    name          = each.key
  }
  depends_on = [aws_lakeformation_permissions.governance_gold]
}

resource "aws_lakeformation_permissions" "operator_gold" {
  provider    = aws.governance
  for_each    = local.analyst_gold
  principal   = aws_iam_role.terraform_operator.arn
  permissions = ["SELECT", "DESCRIBE"]
  table {
    database_name = aws_glue_catalog_database.lakehouse.name
    name          = each.key
  }
  depends_on = [aws_lakeformation_permissions.governance_gold]
}

resource "aws_lakeformation_permissions" "analyst_gold" {
  provider    = aws.governance
  for_each    = local.analyst_gold
  principal   = aws_iam_role.analyst[0].arn
  permissions = ["SELECT", "DESCRIBE"]
  table {
    database_name = aws_glue_catalog_database.lakehouse.name
    name          = each.key
  }
  depends_on = [aws_lakeformation_permissions.governance_gold, aws_lakeformation_resource.gold]
}

resource "aws_lakeformation_permissions" "cleanup_database" {
  provider                      = aws.governance
  count                         = var.analyst_access_enabled ? 1 : 0
  principal                     = local.cleanup_role_arn
  permissions                   = ["DESCRIBE"]
  permissions_with_grant_option = ["DESCRIBE"]
  database { name = aws_glue_catalog_database.lakehouse.name }
}

resource "aws_lakeformation_permissions" "governance_database" {
  provider                      = aws.governance
  count                         = var.analyst_access_enabled ? 1 : 0
  principal                     = data.aws_caller_identity.governance[0].arn
  permissions                   = ["DESCRIBE"]
  permissions_with_grant_option = ["DESCRIBE"]
  database { name = aws_glue_catalog_database.lakehouse.name }
  depends_on = [aws_lakeformation_permissions.cleanup_database]
}

resource "aws_lakeformation_permissions" "analyst_database" {
  provider    = aws.governance
  count       = var.analyst_access_enabled ? 1 : 0
  principal   = aws_iam_role.analyst[0].arn
  permissions = ["DESCRIBE"]
  database { name = aws_glue_catalog_database.lakehouse.name }
  depends_on = [aws_lakeformation_permissions.governance_database]
}

resource "aws_lakeformation_permissions" "cleanup_location" {
  provider                      = aws.governance
  for_each                      = local.analyst_gold
  principal                     = local.cleanup_role_arn
  permissions                   = ["DATA_LOCATION_ACCESS"]
  permissions_with_grant_option = ["DATA_LOCATION_ACCESS"]
  data_location { arn = aws_lakeformation_resource.gold[each.key].arn }
}

resource "aws_lakeformation_permissions" "writer_location" {
  provider    = aws.governance
  for_each    = local.analyst_gold
  principal   = aws_iam_role.glue_silver.arn
  permissions = ["DATA_LOCATION_ACCESS"]
  data_location { arn = aws_lakeformation_resource.gold[each.key].arn }
  depends_on = [aws_lakeformation_permissions.governance_location]
}

resource "aws_lakeformation_permissions" "governance_location" {
  provider                      = aws.governance
  for_each                      = local.analyst_gold
  principal                     = data.aws_caller_identity.governance[0].arn
  permissions                   = ["DATA_LOCATION_ACCESS"]
  permissions_with_grant_option = ["DATA_LOCATION_ACCESS"]
  data_location { arn = aws_lakeformation_resource.gold[each.key].arn }
  depends_on = [aws_lakeformation_permissions.cleanup_location]
}

output "analyst_access" {
  value = var.analyst_access_enabled ? {
    role_arn             = aws_iam_role.analyst[0].arn
    workgroup            = aws_athena_workgroup.analyst[0].name
    data_role_arn        = aws_iam_role.lakeformation_data[0].arn
    gold_locations       = local.gold_locations
    governance_principal = data.aws_caller_identity.governance[0].arn
  } : null
}
