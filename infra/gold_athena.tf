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
#
# 여기서 부여하는 Athena/Glue 권한(Resource="*")은 같은 role(aws_iam_role.lambda)을
# 공유하는 infra/pipeline_orchestrator.tf의 dashboard_refresh Lambda도 그대로
# 물려받는다 - 그쪽에 별도로 같은 정책을 중복 추가하지 않았다.
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

# 2026-09-03: 여기 있던 독립 EventBridge 스케줄(gold_compute_athena_schedule,
# 매시 50분 - rule/target/permission)을 제거했다. infra/pipeline_orchestrator.tf의
# pipeline_orchestrator 상태머신이 Silver 완료 직후 이 Lambda를 GoldCompute Task로
# 동기 호출한다 - "Gold는 Silver가 완전히 끝난 뒤에만 시작"이라는 요구사항 때문에,
# 더 이상 시간 오프셋(10분 뒤)에 의존하지 않는다. 이 Lambda 리소스 자체는 그대로
# 재사용된다.

# CloudWatch Alarm: Gold 집계 Lambda 실패 감지. pipeline_orchestrator_failed
# (infra/pipeline_orchestrator.tf)가 상태머신 전체 실행 실패를 잡아주지만, 이
# Lambda 자체의 Errors 지표를 별도로 보는 알람은 그대로 남겨둔다 - 오케스트레이터
# 밖에서(예: 콘솔에서 수동으로) 이 Lambda를 직접 호출한 경우에도 실패를 놓치지
# 않기 위함.
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
