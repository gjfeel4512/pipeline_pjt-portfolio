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
    BucketName  = aws_s3_bucket.bronze.id
    StorageType = "StandardStorage"
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
  ok_actions    = [aws_sns_topic.pipeline_alerts.arn]

  tags = local.common_tags
}

# CloudWatch Alarm: 일일 수집 Lambda 실패 감지 (Lambda 내장 Errors 지표)
resource "aws_cloudwatch_metric_alarm" "daily_collector_errors" {
  alarm_name          = "${local.resource_prefix}-daily-collector-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = "3600" # 1시간
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "일일 수집 Lambda(daily_search_collector)에서 에러 발생"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.daily_search_collector.function_name
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
  ok_actions    = [aws_sns_topic.pipeline_alerts.arn]

  tags = local.common_tags
}

# CloudWatch Alarm: 트렌딩 순위 추적 Lambda 실패 감지 (Lambda 내장 Errors 지표)
resource "aws_cloudwatch_metric_alarm" "trending_rank_tracker_errors" {
  alarm_name          = "${local.resource_prefix}-trending-rank-tracker-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = "3600" # 1시간
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "트렌딩 순위 추적 Lambda(trending_rank_tracker)에서 에러 발생"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.trending_rank_tracker.function_name
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
  ok_actions    = [aws_sns_topic.pipeline_alerts.arn]

  tags = local.common_tags
}

# CloudWatch Log Metric Filter: 에러 감지
resource "aws_cloudwatch_log_metric_filter" "airflow_errors" {
  name           = "${local.resource_prefix}-airflow-errors"
  log_group_name = aws_cloudwatch_log_group.airflow.name
  pattern        = "[ERROR]"

  metric_transformation {
    name      = "AirflowErrorCount"
    namespace = "Pipeline/PJT"
    value     = "1"
  }
}

resource "aws_cloudwatch_log_metric_filter" "etl_errors" {
  name           = "${local.resource_prefix}-etl-errors"
  log_group_name = aws_cloudwatch_log_group.etl.name
  pattern        = "[ERROR]"

  metric_transformation {
    name      = "ETLErrorCount"
    namespace = "Pipeline/PJT"
    value     = "1"
  }
}

# CloudWatch Alarms: 로그 기반 에러 감지 -> SNS
resource "aws_cloudwatch_metric_alarm" "airflow_errors" {
  alarm_name          = "${local.resource_prefix}-airflow-errors-alarm"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "AirflowErrorCount"
  namespace           = "Pipeline/PJT"
  period              = "3600" # 1시간
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Airflow 로그에서 [ERROR] 패턴 감지"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "etl_errors" {
  alarm_name          = "${local.resource_prefix}-etl-errors-alarm"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "ETLErrorCount"
  namespace           = "Pipeline/PJT"
  period              = "3600" # 1시간
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "ETL 로그에서 [ERROR] 패턴 감지"
  treat_missing_data  = "notBreaching"

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]

  tags = local.common_tags
}
