# CloudWatch Log Group: Airflow
resource "aws_cloudwatch_log_group" "airflow" {
  name              = local.log_group_names.airflow
  retention_in_days = var.log_retention_days

  tags = merge(
    local.common_tags,
    {
      Name = "${local.resource_prefix}-airflow-logs"
    }
  )
}

# CloudWatch Log Group: ETL
resource "aws_cloudwatch_log_group" "etl" {
  name              = local.log_group_names.etl
  retention_in_days = var.log_retention_days

  tags = merge(
    local.common_tags,
    {
      Name = "${local.resource_prefix}-etl-logs"
    }
  )
}

# CloudWatch Log Group: Lambda
resource "aws_cloudwatch_log_group" "lambda" {
  name              = local.log_group_names.lambda
  retention_in_days = var.log_retention_days

  tags = merge(
    local.common_tags,
    {
      Name = "${local.resource_prefix}-lambda-logs"
    }
  )
}

# CloudWatch Alarms: S3 버킷 모니터링
resource "aws_cloudwatch_metric_alarm" "s3_bronze_size" {
  alarm_name          = "${local.resource_prefix}-bronze-size-alarm"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "BucketSizeBytes"
  namespace           = "AWS/S3"
  period              = "86400" # 1일
  statistic           = "Average"
  threshold           = 107374182400 # 100GB
  alarm_description   = "Bronze bucket이 100GB를 초과"
  treat_missing_data  = "notBreaching"

  dimensions = {
    BucketName = aws_s3_bucket.bronze.id
    StorageType = "StandardStorage"
  }

  tags = local.common_tags
}

