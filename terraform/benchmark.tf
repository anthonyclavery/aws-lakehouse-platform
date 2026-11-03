locals {
  benchmark_scripts = {
    build   = "jobs/benchmark_partition_pruning.py"
    profile = "src/lakehouse_platform/partition_benchmark.py"
  }
}

resource "aws_s3_object" "benchmark_script" {
  for_each     = local.benchmark_scripts
  bucket       = aws_s3_bucket.lakehouse.id
  key          = "jobs/${basename(each.value)}"
  source       = "${path.module}/../${each.value}"
  etag         = filemd5("${path.module}/../${each.value}")
  content_type = "text/x-python"
  tags         = local.required_tags
}

output "partition_benchmark" {
  value = {
    glue_job   = aws_glue_job.gold["price"].name
    script_uri = "s3://${aws_s3_bucket.lakehouse.id}/${aws_s3_object.benchmark_script["build"].key}"
    module_uri = "s3://${aws_s3_bucket.lakehouse.id}/${aws_s3_object.benchmark_script["profile"].key}"
  }
}
