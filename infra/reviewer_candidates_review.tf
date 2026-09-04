# ============================================================================
# 리뷰어 후보 사람 검토 & 일괄 저장 상태머신 (reviewer_candidate_review)
#
# frontend/scripts/build_cross_category_reviewers.py가 만드는 "리뷰어 후보"(다른
# 카테고리 리뷰인데 category_id=22로 잘못 분류된 채널)는 지금까지 로컬에서 매번
# 다시 계산만 하고, 사람이 확인한 결과를 저장/편입할 방법이 없었다. 이 상태머신은
# 그 검토 단계를 서버리스로 옮긴다:
#
#   GenerateCandidates (Lambda, 동기)
#     -> Silver-rejected(category=22)를 스캔해 재분류 후보를 계산하고
#        review/cross_category_reviewers/<batch_id>/candidates.json에 스냅샷으로 남김
#   HasCandidates (Choice)
#     -> 후보가 0건이면 바로 종료(NoCandidatesFound)
#   WaitForReview (Task, .waitForTaskToken)
#     -> 콜백 토큰을 review_session.json에 남기고 SNS로 알림만 보낸 뒤 "그냥 대기".
#        사람이 로컬에서 scripts/reviewer_review_submit.py를 돌려 승인/거부를
#        일괄로 결정하고 SendTaskSuccess를 보낼 때까지(최대 30일) 여기서 멈춰있는다.
#   ApplyDecisions (Lambda, 동기)
#     -> 승인된 것만 overrides/channel_category_override.json에 반영(read-modify-write,
#        channel_id 기준 upsert) + 이번 배치의 전체 결정을 decisions.json에 감사 로그로 남김.
#
# 범위 제한(2026-09-04 결정): 이 상태머신은 "사람이 검토하고 일괄로 저장"까지만
# 한다 - overrides/channel_category_override.json을 Silver 재처리나 Gold Athena
# 쿼리에서 실제로 읽어서 category_id를 바꿔치기하는 것은 별도 작업으로 남겨뒀다
# (검증 없이 바로 Gold 통계에 편입하면 품질을 해칠 수 있다는 원래
# build_cross_category_reviewers.py의 설계 원칙을 그대로 유지).
#
# EventBridge 자동 스케줄을 붙이지 않았다 - 이건 사람이 검토하고 싶을 때 시작하는
# 온디맨드 작업이라(다른 4개 파이프라인 단계처럼 "때 되면 자동 실행"할 이유가 없음),
# 항상 scripts/reviewer_review_start.py(또는 AWS 콘솔/CLI)로 사람이 직접 시작한다.
#
# WaitForReview에 SendTaskSuccess/SendTaskFailure를 보내는 쪽은 이 상태머신의 IAM
# 역할이 아니라 scripts/reviewer_review_submit.py를 실행하는 사람의 AWS 자격증명이다
# - 그 사람의 IAM 사용자/역할에 states:SendTaskSuccess / states:SendTaskFailure 권한이
# (이 상태머신 ARN에 대해) 있어야 한다. 이 파일은 서비스 역할(Lambda/Step Functions)만
# 관리하므로 사람 IAM 사용자 권한은 별도로 부여해야 한다.
# ============================================================================

# ---- Lambda 1: 후보 계산 (Silver-rejected 스캔) ----
data "archive_file" "reviewer_candidates_generate" {
  type        = "zip"
  source_file = "${path.module}/../lambda/reviewer_candidates_generate.py"
  output_path = "${path.module}/.reviewer_candidates_generate.zip"
}

resource "aws_lambda_function" "reviewer_candidates_generate" {
  function_name    = "${local.resource_prefix}-reviewer-candidates-generate"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "reviewer_candidates_generate.lambda_handler"
  filename         = data.archive_file.reviewer_candidates_generate.output_path
  source_code_hash = data.archive_file.reviewer_candidates_generate.output_base64sha256
  # Silver-rejected 오브젝트를 전부 나열 + 읽어야 해서(백필 포함 누적분) 넉넉하게 5분.
  timeout     = 300
  memory_size = 512

  environment {
    variables = {
      SILVER_BUCKET_NAME = aws_s3_bucket.silver.id
      GOLD_BUCKET_NAME   = aws_s3_bucket.gold.id
    }
  }

  tags = merge(local.common_tags, { Name = "${local.resource_prefix}-reviewer-candidates-generate" })
}

# ---- Lambda 2: 사람 검토 대기 (.waitForTaskToken) ----
data "archive_file" "reviewer_candidates_wait" {
  type        = "zip"
  source_file = "${path.module}/../lambda/reviewer_candidates_wait.py"
  output_path = "${path.module}/.reviewer_candidates_wait.zip"
}

resource "aws_lambda_function" "reviewer_candidates_wait" {
  function_name    = "${local.resource_prefix}-reviewer-candidates-wait"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "reviewer_candidates_wait.lambda_handler"
  filename         = data.archive_file.reviewer_candidates_wait.output_path
  source_code_hash = data.archive_file.reviewer_candidates_wait.output_base64sha256
  # 토큰을 S3에 남기고 SNS 발행만 하고 바로 반환 - 오래 걸릴 일이 없다.
  timeout     = 30
  memory_size = 128

  environment {
    variables = {
      GOLD_BUCKET_NAME = aws_s3_bucket.gold.id
      SNS_TOPIC_ARN    = aws_sns_topic.pipeline_alerts.arn
    }
  }

  tags = merge(local.common_tags, { Name = "${local.resource_prefix}-reviewer-candidates-wait" })
}

# ---- Lambda 3: 승인된 결정 반영 ----
data "archive_file" "reviewer_candidates_apply" {
  type        = "zip"
  source_file = "${path.module}/../lambda/reviewer_candidates_apply.py"
  output_path = "${path.module}/.reviewer_candidates_apply.zip"
}

resource "aws_lambda_function" "reviewer_candidates_apply" {
  function_name    = "${local.resource_prefix}-reviewer-candidates-apply"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "reviewer_candidates_apply.lambda_handler"
  filename         = data.archive_file.reviewer_candidates_apply.output_path
  source_code_hash = data.archive_file.reviewer_candidates_apply.output_base64sha256
  timeout          = 60
  memory_size      = 128

  environment {
    variables = {
      GOLD_BUCKET_NAME = aws_s3_bucket.gold.id
    }
  }

  tags = merge(local.common_tags, { Name = "${local.resource_prefix}-reviewer-candidates-apply" })
}

# 세 Lambda 모두 aws_iam_role.lambda(iam.tf)를 공유한다. bronze/silver/gold
# GetObject/PutObject/ListBucket은 iam.tf의 lambda_s3 정책에 이미 있고,
# gold_athena.tf의 GoldBucketDelete(Sid)가 같은 role에 Gold 버킷 DeleteObject도
# 이미 부여해뒀다(reviewer_candidates_apply.py가 review_session.json을 지울 때 씀) -
# 여기서 새로 추가해야 하는 건 SNS 발행 권한 하나뿐이다.
resource "aws_iam_role_policy" "reviewer_candidates_sns" {
  name = "${local.resource_prefix}-reviewer-candidates-sns-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "PublishReviewNotification"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.pipeline_alerts.arn
      }
    ]
  })
}

# ----------------------------------------------------------------------------
# IAM 역할: 이 상태머신 전용 (pipeline_orchestrator.tf와 같은 패턴 - 상태머신마다
# 자기 역할을 따로 둔다. 기존 aws_iam_role.step_functions을 재사용하지 않는 이유는
# 그 역할의 InvokeLambda 정책 Resource가 search_to_silver 전용 Lambda 2개로
# 하드코딩돼 있어서, 여기서 확장하면 그 상태머신과 무관한 권한이 섞이기 때문)
# ----------------------------------------------------------------------------
resource "aws_iam_role" "reviewer_candidate_review" {
  name = "${local.resource_prefix}-reviewer-candidate-review-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "states.amazonaws.com" }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "reviewer_candidate_review" {
  name = "${local.resource_prefix}-reviewer-candidate-review-policy"
  role = aws_iam_role.reviewer_candidate_review.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeStageLambdas"
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = [
          aws_lambda_function.reviewer_candidates_generate.arn,
          aws_lambda_function.reviewer_candidates_wait.arn,
          aws_lambda_function.reviewer_candidates_apply.arn,
        ]
      },
      {
        Sid    = "Logging"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "reviewer_candidate_review_sfn" {
  name              = "/aws/vendedlogs/states/${local.resource_prefix}-reviewer-candidate-review"
  retention_in_days = 14

  tags = local.common_tags
}

# ----------------------------------------------------------------------------
# 상태머신 정의 (ASL)
# ----------------------------------------------------------------------------
resource "aws_sfn_state_machine" "reviewer_candidate_review" {
  name     = "${local.resource_prefix}-reviewer-candidate-review"
  role_arn = aws_iam_role.reviewer_candidate_review.arn
  type     = "STANDARD"

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.reviewer_candidate_review_sfn.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = jsonencode({
    Comment = "리뷰어 후보(category_id=22 오분류 채널)를 사람이 검토하고 승인된 것만 일괄 저장(overrides/channel_category_override.json)"
    StartAt = "GenerateCandidates"
    States = {
      GenerateCandidates = {
        Type       = "Task"
        Resource   = aws_lambda_function.reviewer_candidates_generate.arn
        ResultPath = "$.generate_result"
        Retry = [
          {
            ErrorEquals     = ["Lambda.TooManyRequestsException", "Lambda.ServiceException"]
            IntervalSeconds = 10
            MaxAttempts     = 2
            BackoffRate     = 2.0
          }
        ]
        Next = "HasCandidates"
      }
      HasCandidates = {
        Type = "Choice"
        Choices = [
          {
            Variable            = "$.generate_result.candidate_count"
            NumericGreaterThan  = 0
            Next                = "WaitForReview"
          }
        ]
        Default = "NoCandidatesFound"
      }
      NoCandidatesFound = {
        Type   = "Pass"
        Result = { message = "이번 배치에는 재분류 후보가 없음 - 검토 단계 스킵" }
        End    = true
      }
      # .waitForTaskToken: 이 Lambda 호출이 끝나도 상태머신은 계속 이 Task에
      # 머무른다 - 외부(scripts/reviewer_review_submit.py)에서 사람 이름으로
      # SendTaskSuccess/SendTaskFailure를 보낼 때까지. TimeoutSeconds(30일)를
      # 넘기면 실행이 실패로 종료되고 pipeline_alerts로 알람이 간다.
      WaitForReview = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke.waitForTaskToken"
        Parameters = {
          FunctionName = aws_lambda_function.reviewer_candidates_wait.function_name
          Payload = {
            "TaskToken.$"      = "$$.Task.Token"
            "BatchId.$"        = "$.generate_result.batch_id"
            "CandidatesKey.$"  = "$.generate_result.candidates_key"
            "CandidateCount.$" = "$.generate_result.candidate_count"
          }
        }
        TimeoutSeconds = 2592000 # 30일
        ResultPath     = "$.review_result"
        Next           = "ApplyDecisions"
      }
      ApplyDecisions = {
        Type     = "Task"
        Resource = aws_lambda_function.reviewer_candidates_apply.arn
        Parameters = {
          "batch_id.$"  = "$.review_result.batch_id"
          "decisions.$" = "$.review_result.decisions"
        }
        ResultPath = "$.apply_result"
        Retry = [
          {
            ErrorEquals     = ["Lambda.TooManyRequestsException", "Lambda.ServiceException"]
            IntervalSeconds = 10
            MaxAttempts     = 2
            BackoffRate     = 2.0
          }
        ]
        End = true
      }
    }
  })

  tags = local.common_tags
}

# ----------------------------------------------------------------------------
# CloudWatch 알람: 실행 실패 감지. 이 상태머신은 온디맨드라(고정 스케줄 없음)
# pipeline_orchestrator_failed처럼 "N시간마다 1회"를 가정한 좁은 period를 쓸 수
# 없어서, 하루 단위로 넉넉하게 잡았다. TimeoutSeconds(30일) 만료로 인한 실패도
# 이 알람이 그대로 잡아준다.
# ----------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "reviewer_candidate_review_failed" {
  alarm_name          = "${local.resource_prefix}-reviewer-candidate-review-failed"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "ExecutionsFailed"
  namespace           = "AWS/States"
  period              = "86400"
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "리뷰어 후보 검토 상태머신(reviewer_candidate_review) 실행 실패 - 검토 타임아웃(30일) 또는 Lambda 오류. Step Functions 콘솔의 실행 이력에서 확인"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.reviewer_candidate_review.arn
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
  ok_actions    = [aws_sns_topic.pipeline_alerts.arn]

  tags = local.common_tags
}

output "reviewer_candidate_review_state_machine_arn" {
  description = "리뷰어 후보 검토 상태머신 ARN - scripts/reviewer_review_start.py 실행 시 사용"
  value       = aws_sfn_state_machine.reviewer_candidate_review.arn
}
