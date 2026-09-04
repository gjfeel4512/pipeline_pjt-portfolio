# ============================================================================
# 마스터 파이프라인 오케스트레이터 (Bronze -> Silver -> Gold -> Dashboard)
#
# 지금까지는 daily_collector_schedule(매시 30분) / search_to_silver_sfn_schedule
# (매시 40분) / gold_compute_athena_schedule(매시 50분) / refresh_dashboard_schedule
# (Gold 15분 뒤)가 서로 독립된 EventBridge 스케줄로 "각자 알아서" 돌았다. 이 방식은
# 앞 단계가 늦게 끝나거나 실패해도 다음 단계가 시간만 되면 그냥 실행돼버린다는
# 문제가 있다 - 예를 들어 Bronze가 아직 덜 끝났는데 Silver가 40분에 그냥 시작하면
# 그 회차 데이터 일부가 누락된 채로 Silver/Gold/Dashboard까지 흘러간다.
#
# 이 파일은 그 네 스케줄을 하나로 묶어서, 앞 단계가 "완전히 끝난 뒤에만" 다음
# 단계가 시작하도록 강제하는 단일 Step Functions 상태머신(pipeline_orchestrator)을
# 만든다:
#
#   BronzeCollect (Lambda, 동기 호출)
#     -> SilverTransform (기존 search_to_silver 상태머신을 states:startExecution.sync:2로
#        중첩 실행 - 그 상태머신이 완전히 끝날 때까지 여기서 블로킹)
#     -> GoldCompute (Lambda, 동기 호출)
#     -> DashboardRefresh (Lambda, 동기 호출 - infra/refresh_dashboard.tf의
#        refresh_dashboard Lambda를 그대로 재사용) -> 종료
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
# 기존 4개 Lambda/상태머신 리소스 자체는 그대로 재사용한다(daily_search_collector,
# search_to_silver, gold_compute_athena, refresh_dashboard) - 여기서 새로 만드는 건
# 이들을 순서대로 묶는 상태머신과, 그걸 매시 30분에 한 번 깨우는 EventBridge
# 규칙뿐이다. infra/eventbridge.tf / infra/stepfunctions.tf / infra/gold_athena.tf /
# infra/refresh_dashboard.tf에 있던 개별 EventBridge 스케줄(rule/target/permission)은
# 전부 제거했다 - Lambda/상태머신 리소스 자체는 그대로 둔 채 "누가 언제 트리거하는지"만
# 이 오케스트레이터로 일원화했다.
#
# 히스토리 (같은 날, 2026-09-03):
#   1) 처음엔 이 상태머신에 네 번째 단계로 자체 dashboard_refresh Lambda
#      (lambda/dashboard_refresh.py)를 새로 만들어 추가했는데, 팀원이 별도로 이미
#      커밋·병합해둔 infra/refresh_dashboard.tf(lambda/refresh_dashboard.py, 기존
#      export_s3_for_dashboard.py/build_dashboard_data.py 재사용)와 완전히 같은 일을
#      중복 구현한 것으로 드러났다(같은 이름의 Lambda를 서로 다른 Terraform 리소스로
#      만들려다 CreateFunction 409 충돌).
#   2) 1차 해결로는 dashboard_refresh Lambda를 제거하고, 이 오케스트레이터를
#      Bronze->Silver->Gold까지만 담당하도록 축소 - Dashboard 갱신은
#      infra/refresh_dashboard.tf가 계속 자기 스케줄(Gold 15분 뒤)로 독립적으로
#      처리하게 뒀다. 하지만 이 방식은 Gold "완료"를 실제로 확인하지 않고 시간만
#      보고 도는 것이라 순서/의존성 보장이 없었다.
#   3) 팀원이 그 트레이드오프를 다시 고쳐서(`git log`: `2f0508d refactor: 대시보드
#      갱신을 pipeline-orchestrator 4번째 상태로 편입`), 새 Lambda를 만드는 대신
#      기존 infra/refresh_dashboard.tf의 refresh_dashboard Lambda를 그대로 재사용해
#      이 상태머신의 네 번째 Task로 편입시켰다 - Lambda 코드 중복 없이 Gold 완료
#      직후 Dashboard 갱신까지 순서가 보장되는 지금 형태로 확정됨.
#      infra/refresh_dashboard.tf 쪽은 독립 스케줄 리소스(event_rule/event_target/
#      lambda_permission) 3개만 제거되고 Lambda/IAM 정책/에러 알람은 그대로 남았다.
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
          aws_lambda_function.refresh_dashboard.arn,
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
# 상태머신 정의 (ASL): Bronze -> Silver(중첩 동기 실행) -> Gold -> Dashboard,
# 전부 순차/동기 - 앞 단계가 완전히 끝나야 다음 단계로 넘어간다.
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
    Comment = "Bronze -> Silver -> Gold -> DashboardRefresh 를 완전 순차/동기로 실행 (앞 단계 완료 전 다음 단계 시작 금지)"
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
          # 2026-09-04: 원래 $$.Execution.Name(부모 오케스트레이터 실행 이름)을 그대로
          # 붙였는데, EventBridge가 스케줄로 실행을 시작할 때 자동 생성하는 실행 이름이
          # UUID 두 개를 밑줄로 이어붙인 형태(예: "0de3052f-...-c7eb2ebf63a9_728c2c74-...-
          # 6fe8f79df8ee", 73자)라서 "bronze-to-silver-" 접두사(17자)를 붙이면 90자가
          # 되어 Step Functions 실행 이름 제한(80자)을 넘겨 ValidationException이 났다.
          # 부모/자식 실행 연결은 Step Functions 콘솔이 .sync 호출이면 자동으로 보여주므로
          # (부모 이름을 자식 이름에 넣지 않아도 추적 가능) 길이가 고정된 UUID로 대체한다.
          "Name.$" = "States.Format('bronze-to-silver-{}', States.UUID())"
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
          },
          {
            # 2026-09-04 13:30 실행이 이걸로 실패해서 수동 복구함(manual-recovery-*):
            # Athena INSERT INTO가 파티션 프로젝션 대상 파티션을 "No partition found
            # with values [...]"로 못 찾는 간헐적 오류(최근 9회 중 1회) - 알려진
            # Athena 파티션 프로젝션 + INSERT 조합의 일관성 이슈로 보임(S3
            # storage.location.template 경로 해석이 INSERT 시점에 아직 안정화 안 된
            # 것으로 추정). gold_compute_athena.py의 run_query()가 실패 시 일반
            # RuntimeError를 던지므로(Lambda 관점에선 처리 안 된 예외 -
            # errorType="RuntimeError") 여기서 짧게 재시도하면 대부분 다음 시도에서
            # 해결됨.
            ErrorEquals     = ["RuntimeError"]
            IntervalSeconds = 20
            MaxAttempts     = 2
            BackoffRate     = 2.0
          }
        ]
        Next = "DashboardRefresh"
      }
      # Gold 집계가 끝난 직후 대시보드 데이터(frontend/mock/*.json) 재생성 + S3 sync +
      # CloudFront 무효화. 별도 EventBridge 타이머 대신 여기서 이어 실행해 Gold와의
      # 순서/의존성을 보장한다 (refresh_dashboard.tf의 스케줄 리소스는 제거됨).
      DashboardRefresh = {
        Type       = "Task"
        Resource   = aws_lambda_function.refresh_dashboard.arn
        ResultPath = "$.dashboard_result"
        Retry = [
          {
            ErrorEquals     = ["Lambda.TooManyRequestsException", "Lambda.ServiceException", "States.Timeout"]
            IntervalSeconds = 15
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
# 시각에 전체 파이프라인이 시작됨). Silver(매시 40분)/Gold(매시 50분)/Dashboard(Gold
# 15분 뒤) 개별 스케줄은 더 이상 필요 없다 - 전부 오케스트레이터 안에서 바로 앞
# 단계 완료 직후 이어서 실행되기 때문.
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
# CloudWatch 알람: 오케스트레이터 실행 실패 감지 (Bronze/Silver/Gold/Dashboard 어느
# 단계에서 실패하든 상태머신 전체 실행이 FAILED로 끝나므로 이 알람 하나로 4단계
# 전부를 커버한다 - 기존 daily_collector_errors/gold_compute_athena_errors 처럼
# Lambda 개별 알람을 추가로 만들 필요 없음). DashboardRefresh 단계가 실패하면
# infra/refresh_dashboard.tf의 refresh_dashboard_errors(Lambda 단위 Errors 지표)도
# 같이 울린다 - 중복이지만 해가 되지 않고, refresh_dashboard Lambda가 오케스트레이터
# 밖에서(예: 콘솔에서 수동으로) 직접 호출된 경우의 실패까지 잡아주는 용도로 남겨둠.
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
  alarm_description   = "파이프라인 오케스트레이터(Bronze->Silver->Gold->Dashboard) 실행 실패 - 어느 단계에서 실패했는지는 CloudWatch Logs(/aws/vendedlogs/states/${local.resource_prefix}-pipeline-orchestrator) 또는 Step Functions 콘솔의 실행 이력에서 확인"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.pipeline_orchestrator.arn
  }

  alarm_actions = [aws_sns_topic.pipeline_alerts.arn]
  ok_actions    = [aws_sns_topic.pipeline_alerts.arn]

  tags = local.common_tags
}
