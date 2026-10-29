data "aws_iam_policy_document" "glue_silver_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "glue_silver" {
  name               = "${local.operator_prefix}-glue-silver"
  assume_role_policy = data.aws_iam_policy_document.glue_silver_assume_role.json
  tags               = merge(local.required_tags, { Purpose = "GlueSilverExecution" })
}

resource "aws_iam_role_policy" "glue_silver" {
  name = "${local.operator_prefix}-glue-silver"
  role = aws_iam_role.glue_silver.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetBucketLocation", "s3:ListBucket"], Resource = aws_s3_bucket.lakehouse.arn },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${aws_s3_bucket.lakehouse.arn}/*" },
      { Effect = "Allow", Action = ["glue:GetDatabase", "glue:GetTable", "glue:CreateTable", "glue:UpdateTable"], Resource = [
        "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:catalog",
        "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:database/${aws_glue_catalog_database.lakehouse.name}",
        "arn:aws:glue:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${aws_glue_catalog_database.lakehouse.name}/*"
      ] },
      { Effect = "Allow", Action = ["logs:CreateLogGroup"], Resource = "*" },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws-glue/jobs/*" }
    ]
  })
}

resource "aws_s3_object" "silver_process_indexes_script" {
  bucket       = aws_s3_bucket.lakehouse.id
  key          = "jobs/silver_process_indexes.py"
  source       = "${path.module}/../jobs/silver_process_indexes.py"
  etag         = filemd5("${path.module}/../jobs/silver_process_indexes.py")
  content_type = "text/x-python"
}

resource "aws_glue_job" "silver_process_indexes" {
  name              = "${local.name_prefix}-silver-process-indexes"
  role_arn          = aws_iam_role.glue_silver.arn
  glue_version      = "5.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = 30
  max_retries       = 0

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${aws_s3_bucket.lakehouse.id}/${aws_s3_object.silver_process_indexes_script.key}"
  }

  execution_property {
    max_concurrent_runs = 1
  }

  default_arguments = {
    "--datalake-formats"                 = "iceberg"
    "--enable-glue-datacatalog"          = "true"
    "--enable-metrics"                   = "true"
    "--enable-continuous-cloudwatch-log" = "true"
    "--TempDir"                          = "s3://${aws_s3_bucket.lakehouse.id}/temporary/glue/"
    "--conf"                             = "spark.sql.extensions=org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions --conf spark.sql.catalog.glue_catalog=org.apache.iceberg.spark.SparkCatalog --conf spark.sql.catalog.glue_catalog.catalog-impl=org.apache.iceberg.aws.glue.GlueCatalog --conf spark.sql.catalog.glue_catalog.io-impl=org.apache.iceberg.aws.s3.S3FileIO --conf spark.sql.catalog.glue_catalog.warehouse=s3://${aws_s3_bucket.lakehouse.id}/warehouse/"
  }
  tags = merge(local.required_tags, { Purpose = "GlueSilverIndexes" })
}
