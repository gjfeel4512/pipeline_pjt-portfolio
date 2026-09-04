# ==============================================================================
# 심화분석 3종(metadata_impact/topic_trends/synthetic_demo, spec.md 분석 5/6/7)
# 자동화. lambda/analysis_refresh.py 참고 - pandas/statsmodels/scikit-learn을
# 쓰는데, 이 조합이 zip 기반 Lambda(레이어 포함 압축 해제 250MB 제한)로는 위험할
# 만큼 커서(보통 300MB+) 컨테이너 이미지 Lambda(최대 10GB, 이 제한 자체가 없음)로
# 배포한다. refresh_dashboard.tf/gold_athena.tf(둘 다 zip 기반)와 배포 방식만
# 다르고 나머지 패턴(공유 IAM Role, EventBridge 스케줄, CloudWatch 알람)은 동일.
#
# 매일 1회로 잡은 이유: Gold(4시간마다)와 달리 이 3개는 Silver만 있으면 되고,
# 회귀/군집 결과가 몇 시간 단위로 크게 안 바뀌는 데다 Athena보다 계산 자체가
# 무거워서(regression/clustering) 굳이 자주 돌릴 필요가 없다.
#
# 빌드/푸시(최초 1회 + 코드 변경 시마다, 리포지토리 루트에서):
#   aws ecr get-login-password --region us-west-2 | \
#     docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-west-2.amazonaws.com
#   docker build -f lambda/Dockerfile.analysis_refresh -t goldline-dev-analysis-refresh .
#   docker tag goldline-dev-analysis-refresh:latest \
#     <account-id>.dkr.ecr.us-west-2.amazonaws.com/goldline-dev-analysis-refresh:latest
#   docker push <account-id>.dkr.ecr.us-west-2.amazonaws.com/goldline-dev-analysis-refresh:latest
#   그 다음 terraform apply (Lambda가 ECR의 최신 이미지 다이제스트를 picks up하도록
#   aws_lambda_function이 data.aws_ecr_image로 다이제스트를 조회해서 image_uri에 반영)
# ==============================================================================

resource "aws_ecr_repository" "analysis_refresh" {
  name                 = "${local.resource_prefix}-analysis-refresh"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.common_tags, { Name = "${local.resource_prefix}-analysis-refresh" })
}

resource "aws_ecr_lifecycle_policy" "analysis_refresh" {
  repository = aws_ecr_repository.analysis_refresh.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "최근 5개만 보관(용량 절약, 이 Lambda는 :latest 태그 하나만 씀)"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

# ECR에 :latest 태그로 푸시된 이미지의 실제 다이제스트를 조회 - aws_lambda_function의
# image_uri는 태그가 아니라 다이제스트로 고정해야 새 이미지를 푸시했을 때
# source_code_hash 없이도(컨테이너 이미지는 그 필드가 없음) terraform apply가
# 변경을 감지해서 Lambda를 업데이트한다.
data "aws_ecr_image" "analysis_refresh_latest" {
  repository_name = aws_ecr_repository.analysis_refresh.name
  image_tag       = "latest"
}

resource "aws_lambda_function" "analysis_refresh" {
  function_name = "${local.resource_prefix}-analysis-refresh"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.analysis_refresh.repository_url}@${data.aws_ecr_image.analysis_refresh_latest.image_digest}"
  # 3개 스크립트 순차 실행(회귀 3개 카테고리 + TF-IDF/KMeans 군집 3개 카테고리 +
  # 합성 데이터 생성) - 로컬 실측으로도 몇 초~수십 초대라 5분이면 충분히 여유 있음.
  timeout     = 300
  memory_size = 1024

  environment {
    variables = {
      AWS_S3_SILVER_BUCKET       = aws_s3_bucket.silver.id
      FRONTEND_S3_BUCKET         = aws_s3_bucket.frontend.id
      CLOUDFRONT_DISTRIBUTION_ID = aws_cloudfront_distribution.frontend.id
    }
  }

  tags = merge(local.common_tags, { Name = "${local.resource_prefix}-analysis-refresh" })
}

# S3 프론트엔드 버킷 쓰기 + CloudFront 무효화는 aws_iam_role_policy.refresh_dashboard
# (refresh_dashboard.tf)가 같은 공유 aws_iam_role.lambda에 이미 붙여놔서 여기서 또
# 안 붙여도 됨. Silver 읽기도 aws_iam_policy.lambda_s3(iam.tf)로 이미 있음.

resource "aws_cloudwatch_event_rule" "analysis_refresh_schedule" {
  name                = "${local.resource_prefix}-analysis-refresh-schedule"
  description         = "심화분석 3종(metadata_impact/topic_trends/synthetic_demo) 갱신 - 매일 1회"
  schedule_expression = "cron(15 22 * * ? *)" # UTC 22:15 = KST 07:15(익일)

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "analysis_refresh_target" {
  rule      = aws_cloudwatch_event_rule.analysis_refresh_schedule.name
  target_id = "${local.resource_prefix}-analysis-refresh"
  arn       = aws_lambda_function.analysis_refresh.arn
}

resource "aws_lambda_permission" "allow_eventbridge_analysis_refresh" {
  statement_id  = "AllowExecutionFromEventBridgeAnalysisRefresh"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.analysis_refresh.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.analysis_refresh_schedule.arn
}

resource "aws_cloudwatch_metric_alarm" "analysis_refresh_errors" {
  alarm_name          = "${local.resource_prefix}-analysis-refresh-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = "3600"
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "심화분석 갱신 Lambda(analysis_refresh)에서 에러 발생"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.analysis_refresh.function_name
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
  ok_actions    = [aws_sns_topic.pipeline_alerts.arn]

  tags = local.common_tags
}

output "analysis_refresh_ecr_repository_url" {
  description = "docker push 대상 (빌드/푸시 안내는 파일 상단 주석 참고)"
  value       = aws_ecr_repository.analysis_refresh.repository_url
}
