resource "aws_athena_workgroup" "demo" {
  name          = "${local.name_prefix}-qualification"
  force_destroy = true
  configuration {
    enforce_workgroup_configuration    = true
    bytes_scanned_cutoff_per_query     = 1073741824
    publish_cloudwatch_metrics_enabled = true
    engine_version {
      selected_engine_version = "Athena engine version 3"
    }
    result_configuration {
      output_location = "s3://${aws_s3_bucket.lakehouse.id}/athena-results/"
      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
  tags = local.required_tags
}

output "athena_workgroup_name" {
  value = aws_athena_workgroup.demo.name
}
