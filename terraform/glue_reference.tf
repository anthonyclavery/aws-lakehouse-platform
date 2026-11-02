resource "aws_s3_object" "silver_process_reference_script" {
  bucket       = aws_s3_bucket.lakehouse.id
  key          = "jobs/silver_process_reference.py"
  source       = "${path.module}/../jobs/silver_process_reference.py"
  etag         = filemd5("${path.module}/../jobs/silver_process_reference.py")
  content_type = "text/x-python"
  tags         = local.required_tags
}

resource "aws_glue_job" "silver_process_reference" {
  name              = "${local.name_prefix}-silver-process-reference"
  role_arn          = aws_iam_role.glue_silver.arn
  glue_version      = "5.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = 30
  max_retries       = 0

  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${aws_s3_bucket.lakehouse.id}/${aws_s3_object.silver_process_reference_script.key}"
  }
  execution_property {
    max_concurrent_runs = 1
  }
  default_arguments = merge(aws_glue_job.silver_process_indexes.default_arguments, {
    "--job-bookmark-option" = "job-bookmark-disable"
  })
  tags       = merge(local.required_tags, { Purpose = "GlueSilverReference" })
  depends_on = [aws_iam_role_policy.glue_silver]
}

output "silver_reference_job_name" {
  value = aws_glue_job.silver_process_reference.name
}
