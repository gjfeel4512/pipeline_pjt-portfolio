# ============================================================================
# 마스터 파이프라인 오케스트레이터 (Bronze -> Silver -> Gold)
#
# 지금까지는 daily_collector_schedule(매시 30분) / search_to_silver_sfn_schedule
# (매시 40분) / gold_compute_athena_schedule(매시 50분)이 서로 독립된 EventBridge
# 스케줄로 "각자 알아서" 돌았다. 이 방식은 앞 단계가 늦게 끝나거나 실패해도 다음
# 단계가 시간만 되면 그냥 실행돼버린다는 문제가 있다 - 예를 들어 Bronze가 아직 덜
# 끝났는데 Silver가 40분에 그냥 시작하면 그 회차 데이터 일부가 누락된 채로 Silver/
# Gold까지 흘러간다.
#
# 이 파일은 그 세 스케줄을 하나로 묶어서, 앞 단계가 "완전히 끝난 뒤에만" 다음
# 단계가 시작하도록 강제하는 단일 Step Functions 상태머신(pipeline_orchestrator)을
# 만든다:
#
#   BronzeCollect (Lambda, 동기 호출)
#     -> SilverTransform (기존 search_to_silver 상태머신을 states:startExecution.sync:2로
#        중첩 실행 - 그 상태머신이 완전히 끝날 때까지 여기서 블로킹)
#     -> GoldCompute (Lambda, 동기 호출) -> 종료
#
# 각 Task는 동기(Lambda 기본 호출 방식도 동기, Step Functions Task도 기본이 동기)라
# 앞 상태가 성공적으로 끝나야만 Next로 넘어간다. 어느 하나라도 실패하면(예외 발생)
# Catch 없이 그대로 실행 전체가 FAILED로 끝나서 아래 pipeline_orchestrator_failed
# 알람이 울린다 - 뒷 단계는 아예 시작되지 않는다.
#
# BronzeCollect이 안전하게 동기 호출 가능한 이유: lambda/youtube_api_daily.py는
# 자체 시간예산(TIME_BUDGET_SAFETY_SEC)이 부족하면 그때까지 모은 것만 저장하고
# "성공"으로 종료하도록 설계돼 있다(infra/lambda.tf 주석 참고) - 즉 이 Lambda의 한
# 번 호출은 항상 "이번 회차 Bronze 작업"의 완결된 단위를 의미하므로, Step Functions가
# 그 종료를 그대로 "Bronze 완료" 신호로 믿어도 된다.
#
# 기존 3개 Lambda/상태머신 리소스 자체는 그대로 재사용한다(daily_search_collector,
# search_to_silver, gold_compute_athena) - 여기서 새로 만드는 건 이들을 순서대로
# 묶는 상태머신과, 그걸 매시 30분에 한 번 깨우는 EventBridge 규칙뿐이다.
# infra/eventbridge.tf / infra/stepfunctions.tf / infra/gold_athena.tf에 있던
# 개별 EventBridge 스케줄(rule/target/permission)은 이 파일과 함께 제거했다 -
# Lambda/상태머신 리소스 자체는 그대로 둔 채 "누가 언제 트리거하는지"만 이
# 오케스트레이터로 일원화했다.
#
# 2026-09-03(같은 날, 나중): 원래 이 상태머신에 네 번째 단계로 DashboardRefresh
# (lambda/dashboard_refresh.py, Silver/Gold -> frontend mock/*.json -> CloudFront
# 무효화)를 추가했었는데, 팀원이 별도로 이미 커밋·병합해둔 infra/refresh_dashboard.tf
# (lambda/refresh_dashboard.py, 기존 export_s3_for_dashboard.py/build_dashboard_data.py를
# 재사용하는 독립 EventBridge 스케줄 방식)와 이름이 겹치는 걸 발견했다(같은 함수를
# Terraform이 서로 다른 리소스로 만들려다 CreateFunction 409 충돌 발생 - 팀원 쪽
# state에서 먼저 apply된 상태). 두 구현 중 팀원 것을 유지하기로 결정 - 이 오케스트레이터는
# Bronze -> Silver -> Gold까지만 담당하고, Dashboard 갱신은 infra/refresh_dashboard.tf가
# 계속 자기 스케줄(Gold 스케줄 15분 뒤)로 별도 처리한다. 그래서 dashboard_refresh Lambda/
# IAM 정책 리소스는 이 파일에서 제거했고, lambda/dashboard_refresh.py도 더 이상
# 어디서도 참조되지 않아 삭제했다. (주의: infra/refresh_dashboard.tf의 스케줄은
# Gold "완료"를 실제로 확인하지 않고 시간만 보고 도는 방식이라, 이 오케스트레이터가
# Bronze/Silver/Gold에 대해 보장하는 것과 같은 수준의 순서 보장은 없음 - 팀 결정으로
# 감수하기로 함.)
# ============================================================================

# ----------------------------------------------------------------------------
# IAM 역할: 마스터 오케스트레이터 상태머신용
# ----------------------------------------------------------------------------
resource "aws_iam_role" "pipeline_orchestrator" {
  name = "${local.resource_prefix}-pipeline-orchestrator-role"

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

resource "aws_iam_role_policy" "pipeline_orchestrator" {
  name = "${local.resource_prefix}-pipeline-orchestrator-policy"
  role = aws_iam_role.pipeline_orchestrator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeStageLambdas"
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = [
          aws_lambda_function.daily_search_collector.arn,
          aws_lambda_function.gold_compute_athena.arn,
        ]
      },
      {
        # SilverTransform Task가 arn:aws:states:::states:startExecution.sync:2로
        # 기존 search_to_silver 상태머신을 중첩 실행하고 완료까지 대기하는 데 필요.
        Sid    = "RunSilverChildExecution"
        Effect = "Allow"
        Action = [
          "states:StartExecution",
          "states:DescribeExecution",
          "states:StopExecution",
        ]
        Resource = [
          aws_sfn_state_machine.search_to_silver.arn,
          "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:execution:${aws_sfn_state_machine.search_to_silver.name}:*",
        ]
      },
      {
        # .sync/.sync:2 패턴이 자식 실행 완료를 EventBridge를 통해 통지받기 위해
        # AWS가 문서화한 필수 권한 (StepFunctionsGetEventsForStepFunctionsExecutionRule
        # 관리형 규칙에 대한 권한). 이게 없으면 SilverTransform Task가 자식 실행이
        # 끝나도 영원히 대기 상태로 멈춘다.
        Sid    = "AllowSyncChildExecutionEvents"
        Effect = "Allow"
        Action = [
          "events:PutTargets",
          "events:PutRule",
          "events:DescribeRule",
        ]
        Resource = "arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsForStepFunctionsExecutionRule"
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

resource "aws_cloudwatch_log_group" "pipeline_orchestrator_sfn" {
  name              = "/aws/vendedlogs/states/${local.resource_prefix}-pipeline-orchestrator"
  retention_in_days = 14

  tags = local.common_tags
}

# ----------------------------------------------------------------------------
# 상태머신 정의 (ASL): Bronze -> Silver(중첩 동기 실행) -> Gold,
# 전부 순차/동기 - 앞 단계가 완전히 끝나야 다음 단계로 넘어간다. (Dashboard 갱신은
# infra/refresh_dashboard.tf가 별도 스케줄로 담당 - 위 파일 상단 주석 참고)
# ----------------------------------------------------------------------------
resource "aws_sfn_state_machine" "pipeline_orchestrator" {
  name     = "${local.resource_prefix}-pipeline-orchestrator"
  role_arn = aws_iam_role.pipeline_orchestrator.arn
  type     = "STANDARD"

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.pipeline_orchestrator_sfn.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = jsonencode({
    Comment = "Bronze -> Silver -> Gold 를 완전 순차/동기로 실행 (앞 단계 완료 전 다음 단계 시작 금지). Dashboard 갱신은 infra/refresh_dashboard.tf가 별도 스케줄로 담당"
    StartAt = "BronzeCollect"
    States = {
      BronzeCollect = {
        Type       = "Task"
        Resource   = aws_lambda_function.daily_search_collector.arn
        ResultPath = "$.bronze_result"
        Retry = [
          {
            ErrorEquals     = ["Lambda.TooManyRequestsException", "Lambda.ServiceException"]
            IntervalSeconds = 10
            MaxAttempts     = 2
            BackoffRate     = 2.0
          }
        ]
        Next = "SilverTransform"
      }
      SilverTransform = {
        Type     = "Task"
        Resource = "arn:aws:states:::states:startExecution.sync:2"
        Parameters = {
          StateMachineArn = aws_sfn_state_machine.search_to_silver.arn
          "Name.$"        = "States.Format('bronze-to-silver-{}', $$.Execution.Name)"
        }
        ResultPath = "$.silver_result"
        Next       = "GoldCompute"
      }
      GoldCompute = {
        Type       = "Task"
        Resource   = aws_lambda_function.gold_compute_athena.arn
        ResultPath = "$.gold_result"
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
# EventBridge: 4시간마다 매시 30분에 오케스트레이터 시작 (기존 daily_collector_schedule
# 스케줄식을 그대로 재사용 - Bronze가 이제 이 상태머신의 첫 Task이므로 예전과 같은
# 시각에 전체 파이프라인이 시작됨). Silver(매시 40분)/Gold(매시 50분) 스케줄은
# 더 이상 필요 없다 - 각각 오케스트레이터 안에서 Bronze/Silver 완료 직후 바로
# 이어서 실행되기 때문. (Dashboard 갱신 스케줄은 infra/refresh_dashboard.tf가 별도로 유지)
# ----------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "pipeline_orchestrator_schedule" {
  name                = "${local.resource_prefix}-pipeline-orchestrator-schedule"
  description         = "Bronze->Silver->Gold 마스터 오케스트레이터 스케줄 (구 daily_collector_schedule과 동일 주기)"
  schedule_expression = var.daily_collector_schedule_expression

  tags = local.common_tags
}

resource "aws_iam_role" "eventbridge_pipeline_orchestrator" {
  name = "${local.resource_prefix}-eventbridge-pipeline-orchestrator-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action    = "sts:AssumeRole"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "eventbridge_start_pipeline_orchestrator" {
  name = "${local.resource_prefix}-eventbridge-start-pipeline-orchestrator"
  role = aws_iam_role.eventbridge_pipeline_orchestrator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = aws_sfn_state_machine.pipeline_orchestrator.arn
      }
    ]
  })
}

resource "aws_cloudwatch_event_target" "pipeline_orchestrator_target" {
  rule      = aws_cloudwatch_event_rule.pipeline_orchestrator_schedule.name
  target_id = "${local.resource_prefix}-pipeline-orchestrator"
  arn       = aws_sfn_state_machine.pipeline_orchestrator.arn
  role_arn  = aws_iam_role.eventbridge_pipeline_orchestrator.arn
}

# ----------------------------------------------------------------------------
# CloudWatch 알람: 오케스트레이터 실행 실패 감지 (Bronze/Silver/Gold 어느 단계에서
# 실패하든 상태머신 전체 실행이 FAILED로 끝나므로 이 알람 하나로 3단계 전부를
# 커버한다 - 기존 daily_collector_errors/gold_compute_athena_errors 처럼 Lambda
# 개별 알람을 추가로 만들 필요 없음). Dashboard 갱신 실패는 infra/refresh_dashboard.tf의
# refresh_dashboard_errors 알람이 별도로 담당.
# ----------------------------------------------------------------------------
resource "aws_cloudwatch_metric_alarm" "pipeline_orchestrator_failed" {
  alarm_name          = "${local.resource_prefix}-pipeline-orchestrator-failed"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "1"
  metric_name         = "ExecutionsFailed"
  namespace           = "AWS/States"
  period              = "3600" # 1시간 (오케스트레이터는 4시간마다 1회 실행)
  statistic           = "Sum"
  threshold           = 0
  alarm_description   = "파이프라인 오케스트레이터(Bronze->Silver->Gold) 실행 실패 - 어느 단계에서 실패했는지는 CloudWatch Logs(/aws/vendedlogs/states/${local.resource_prefix}-pipeline-orchestrator) 또는 Step Functions 콘솔의 실행 이력에서 확인"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.pipeline_orchestrator.arn
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
  ok_actions    = [aws_sns_topic.pipeline_alerts.arn]

  tags = local.common_tags
}
