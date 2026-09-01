# S3 버킷 출력
output "s3_bucket_names" {
  description = "생성된 S3 버킷 이름들"
  value = {
    bronze     = aws_s3_bucket.bronze.id
    silver     = aws_s3_bucket.silver.id
    gold       = aws_s3_bucket.gold.id
  }
}

output "s3_bucket_arns" {
  description = "생성된 S3 버킷 ARN들"
  value = {
    bronze     = aws_s3_bucket.bronze.arn
    silver     = aws_s3_bucket.silver.arn
    gold       = aws_s3_bucket.gold.arn
  }
}

# IAM 역할 출력
output "iam_role_arns" {
  description = "생성된 IAM 역할 ARN들"
  value = {
    airflow = aws_iam_role.airflow.arn
    lambda  = aws_iam_role.lambda.arn
  }
}

# CloudWatch 로그 그룹 출력
output "cloudwatch_log_groups" {
  description = "생성된 CloudWatch 로그 그룹"
  value = {
    airflow = aws_cloudwatch_log_group.airflow.name
    etl     = aws_cloudwatch_log_group.etl.name
    lambda  = aws_cloudwatch_log_group.lambda.name
  }
}

# 환경 정보
output "deployment_info" {
  description = "배포 정보"
  value = {
    region      = var.aws_region
    environment = var.environment
    account_id  = data.aws_caller_identity.current.account_id
  }
}

# Airflow 설정용 환경변수
output "airflow_env_vars" {
  description = "Airflow에 설정할 환경변수"
  value       = <<-EOT
export AWS_DEFAULT_REGION=${var.aws_region}
export AWS_S3_BRONZE_BUCKET=${aws_s3_bucket.bronze.id}
export AWS_S3_SILVER_BUCKET=${aws_s3_bucket.silver.id}
export AWS_S3_GOLD_BUCKET=${aws_s3_bucket.gold.id}
export AWS_IAM_ROLE_ARN=${aws_iam_role.airflow.arn}
export AWS_CLOUDWATCH_LOG_GROUP=${aws_cloudwatch_log_group.airflow.name}
  EOT
}

output "firehose_stream_name" {
  value = aws_kinesis_firehose_delivery_stream.bronze.name
}

output "glue_database_name" {
  value = aws_glue_catalog_database.pipeline.name
}
