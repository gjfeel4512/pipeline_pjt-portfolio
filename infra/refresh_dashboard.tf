# ==============================================================================
# 대시보드 데이터 갱신: Silver/Gold -> frontend/mock/*.json -> 프론트엔드 S3 ->
# CloudFront 무효화. gold_athena.tf(Lambda+EventBridge)와 동일한 패턴.
#
# 원래 이 작업은 .github/workflows/refresh-dashboard-data.yml(GitHub Actions)이
# 했었다. mock/*.json을 git에 커밋해서 다들 pull 받아 보게 하려던 것이었는데,
# infra/frontend.tf로 CloudFront 배포가 생긴 뒤로는 그 이유가 사라졌고, 대신
# AWS 자격증명을 GitHub Secrets에 export해야 하는 부담만 남았다(실제로 한 번도
# 등록된 적이 없어서 워크플로가 실행 자체가 안 됐음 - workflow_dispatch로 수동
# 실행해서 확인). Lambda는 실행 역할(IAM Role)로 인증하므로 자격증명을 어디에도
# 등록할 필요가 없어서, 이 프로젝트의 다른 스케줄 작업들과 같은 방식으로 옮긴다.
# GitHub Actions 워크플로는 삭제한다(같은 일을 두 곳에서 하지 않도록).
# ==============================================================================

data "archive_file" "refresh_dashboard" {
  type        = "zip"
  output_path = "${path.module}/.refresh_dashboard.zip"

  source {
    content  = file("${path.module}/../lambda/refresh_dashboard.py")
    filename = "refresh_dashboard.py"
  }
  # frontend/scripts/의 기존 스크립트를 그대로 재사용(복제/재작성 안 함) - 로컬
  # 개발자용 export_s3_for_dashboard.py/build_dashboard_data.py와 Lambda가 항상
  # 같은 코드를 실행하도록 보장한다. 배포 패키지 안에서도 frontend/scripts/ 라는
  # 같은 상대 경로에 두는 이유는, 두 스크립트가 __file__ 위치를 기준으로
  # outputs/, frontend/mock/ 경로를 계산하기 때문(lambda/refresh_dashboard.py의
  # _prepare_writable_copy() 참고 - 실행 전에 /tmp로 복사해서 그 계산이 쓰기
  # 가능한 위치를 가리키게 만든다).
  source {
    content  = file("${path.module}/../frontend/scripts/export_s3_for_dashboard.py")
    filename = "frontend/scripts/export_s3_for_dashboard.py"
  }
  source {
    content  = file("${path.module}/../frontend/scripts/build_dashboard_data.py")
    filename = "frontend/scripts/build_dashboard_data.py"
  }
}

resource "aws_lambda_function" "refresh_dashboard" {
  function_name    = "${local.resource_prefix}-refresh-dashboard"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "refresh_dashboard.lambda_handler"
  filename         = data.archive_file.refresh_dashboard.output_path
  source_code_hash = data.archive_file.refresh_dashboard.output_base64sha256
  # Silver 3개 카테고리 S3 페이지네이션 읽기 + Athena 쿼리 3개(제출+폴링) +
  # mock/*.json 8개 업로드 + CloudFront invalidation. 넉넉하게 5분.
  timeout     = 300
  memory_size = 512

  environment {
    variables = {
      AWS_S3_SILVER_BUCKET       = aws_s3_bucket.silver.id
      AWS_S3_GOLD_BUCKET         = aws_s3_bucket.gold.id
      AWS_ATHENA_DATABASE        = aws_glue_catalog_database.pipeline.name
      AWS_ATHENA_OUTPUT_LOCATION = "s3://${aws_s3_bucket.gold.id}/athena-query-results/"
      FRONTEND_S3_BUCKET         = aws_s3_bucket.frontend.id
      CLOUDFRONT_DISTRIBUTION_ID = aws_cloudfront_distribution.frontend.id
    }
  }

  tags = merge(
    local.common_tags,
    {
      Name = "${local.resource_prefix}-refresh-dashboard"
    }
  )
}

# aws_iam_role.lambda(iam.tf)는 이미 Bronze/Silver/Gold S3 GetObject/PutObject/
# ListBucket을 갖고 있고, gold_compute_athena.tf의 정책이 같은 role에 Athena
# 쿼리/Glue 카탈로그 조회 권한도 이미 붙여놨다(공유 role이라 여기서 또 안 붙여도
# 됨). 여기서는 아직 아무 Lambda도 갖고 있지 않던 두 가지만 추가한다: 프론트엔드
# S3 버킷 쓰기, CloudFront 무효화.
resource "aws_iam_role_policy" "refresh_dashboard" {
  name = "${local.resource_prefix}-refresh-dashboard-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "FrontendBucketWrite"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.frontend.arn,
          "${aws_s3_bucket.frontend.arn}/*"
        ]
      },
      {
        Sid    = "CloudFrontInvalidate"
        Effect = "Allow"
        Action = [
          "cloudfront:CreateInvalidation"
        ]
        Resource = aws_cloudfront_distribution.frontend.arn
      }
    ]
  })
}

# 이 Lambda는 이제 별도 EventBridge 타이머가 아니라 pipeline_orchestrator 상태머신의
# 마지막 상태(DashboardRefresh)로 호출된다 - Gold 집계 완료 직후 이어서 실행되므로
# 순서/의존성이 보장된다. (구 refresh_dashboard_schedule / target / lambda_permission
# 리소스는 infra/pipeline_orchestrator.tf로 편입하며 제거함.)

# CloudWatch Alarm: 대시보드 갱신 Lambda 실패 감지
resource "aws_cloudwatch_metric_alarm" "refresh_dashboard_errors" {
  alarm_name          = "${local.resource_prefix}-refresh-dashboard-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = "3600"
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "대시보드 데이터 갱신 Lambda(refresh_dashboard)에서 에러 발생"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.refresh_dashboard.function_name
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
  ok_actions    = [aws_sns_topic.pipeline_alerts.arn]

  tags = local.common_tags
}

output "refresh_dashboard_lambda_name" {
  description = "수동 트리거용: aws lambda invoke --function-name <이 값> out.json"
  value       = aws_lambda_function.refresh_dashboard.function_name
}
