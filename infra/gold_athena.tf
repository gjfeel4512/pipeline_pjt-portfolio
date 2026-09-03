# ============================================================================
# Gold 파이프라인: PostgreSQL(silver_to_gold_dag.py, RDS 없음) 대신
# Athena(Glue Catalog의 silver_youtube)로 Gold 3종을 직접 계산해서
# S3 Gold 버킷에 쓰는 서버리스 경로. RDS/Docker Postgres가 전혀 필요 없다.
#
# lambda/gold_compute_athena.py 참고 (sql/compute_gold.sql을 Presto/Trino SQL로
# 이식한 것). gold_video_rank_trend는 이식 대상에서 제외 - 유일한 데이터 소스였던
# trending_rank_tracker.py가 삭제되어(2026-09-03) trending_rank가 더 이상
# 채워지지 않음.
# ============================================================================

data "archive_file" "gold_compute_athena" {
  type        = "zip"
  source_file = "${path.module}/../lambda/gold_compute_athena.py"
  output_path = "${path.module}/.gold_compute_athena.zip"
}

resource "aws_lambda_function" "gold_compute_athena" {
  function_name    = "${local.resource_prefix}-gold-compute-athena"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "gold_compute_athena.lambda_handler"
  filename         = data.archive_file.gold_compute_athena.output_path
  source_code_hash = data.archive_file.gold_compute_athena.output_base64sha256
  # Athena 쿼리 3개(제출 + 폴링) 순차 실행 - 표본 규모에서는 각각 수 초면
  # 끝나지만, 넉넉하게 5분 잡아둔다.
  timeout     = 300
  memory_size = 256

  environment {
    variables = {
      ATHENA_DATABASE        = aws_glue_catalog_database.pipeline.name
      ATHENA_OUTPUT_LOCATION = "s3://${aws_s3_bucket.gold.id}/athena-query-results/"
      GOLD_BUCKET_NAME       = aws_s3_bucket.gold.id
    }
  }

  tags = merge(
    local.common_tags,
    {
      Name = "${local.resource_prefix}-gold-compute-athena"
    }
  )
}

# 기존 aws_iam_role.lambda(iam.tf)가 갖고 있던 S3 권한(GetObject/PutObject/
# ListBucket)에 없던 것만 추가: Athena 쿼리 실행, Glue 카탈로그 조회,
# Gold 버킷 DeleteObject(멱등 재실행을 위한 파티션 purge용).
resource "aws_iam_role_policy" "gold_compute_athena" {
  name = "${local.resource_prefix}-gold-compute-athena-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AthenaQuery"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution",
          "athena:GetWorkGroup"
        ]
        Resource = "*"
      },
      {
        Sid    = "GlueCatalogRead"
        Effect = "Allow"
        Action = [
          "glue:GetTable",
          "glue:GetTables",
          "glue:GetDatabase",
          "glue:GetPartitions"
        ]
        Resource = "*"
      },
      {
        Sid    = "GoldBucketDelete"
        Effect = "Allow"
        Action = [
          "s3:DeleteObject"
        ]
        Resource = "${aws_s3_bucket.gold.arn}/*"
      },
      {
        Sid    = "BucketLocationForAthena"
        Effect = "Allow"
        Action = [
          "s3:GetBucketLocation"
        ]
        Resource = [
          aws_s3_bucket.silver.arn,
          aws_s3_bucket.gold.arn
        ]
      }
    ]
  })
}

# EventBridge 스케줄: 4시간마다 매시 50분.
# 2026-09-03 기준 실제 파이프라인 주기에 맞춘 것 - infra/variables.tf의
# daily_collector_schedule_expression(브론즈 수집, "30 */4 * * ? *")이 팀원에 의해
# 4시간 주기로 조정되었고, infra/stepfunctions.tf의 search_to_silver_sfn_schedule
# (Silver 변환, "40 */4 * * ? *")도 같은 주기다. Gold는 그 10분 뒤(브론즈 30분 ->
# 실버 40분 -> 골드 50분, 매 4시간)로 맞춘다 - 예전 silver_to_gold_dag.py의
# "매시 50분"은 Silver가 매시간 갱신되던 시절(지금은 죽은 daily_lambda_to_silver_dag
# 기준) 값이라 그대로 쓰면 Silver가 갱신되지 않는 3번 중 2번은 헛돌게 된다.
resource "aws_cloudwatch_event_rule" "gold_compute_athena_schedule" {
  name                = "${local.resource_prefix}-gold-compute-athena-schedule"
  description         = "Athena 기반 Gold 집계(gold_compute_athena) Lambda 스케줄 - 4시간마다 매시 50분(Silver 변환 10분 뒤)"
  schedule_expression = "cron(50 */4 * * ? *)"

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "gold_compute_athena_target" {
  rule      = aws_cloudwatch_event_rule.gold_compute_athena_schedule.name
  target_id = "${local.resource_prefix}-gold-compute-athena"
  arn       = aws_lambda_function.gold_compute_athena.arn
}

resource "aws_lambda_permission" "allow_eventbridge_gold_compute_athena" {
  statement_id  = "AllowExecutionFromEventBridgeGoldComputeAthena"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gold_compute_athena.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.gold_compute_athena_schedule.arn
}

# CloudWatch Alarm: Gold 집계 Lambda 실패 감지
resource "aws_cloudwatch_metric_alarm" "gold_compute_athena_errors" {
  alarm_name          = "${local.resource_prefix}-gold-compute-athena-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = "3600"
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "Athena 기반 Gold 집계 Lambda(gold_compute_athena)에서 에러 발생"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.gold_compute_athena.function_name
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
  ok_actions    = [aws_sns_topic.pipeline_alerts.arn]

  tags = local.common_tags
}
