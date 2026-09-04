# ==============================================================================
# 수치 모니터링: CloudWatch 알람 8개는 전부 "성공/실패"(임계값 초과 여부)만 본다.
# 실행은 성공했지만 사실상 이상한 상황(쿼터를 비정상적으로 많이 씀, Bronze에
# 0건만 씀, Athena 쿼리가 점점 더 많은 데이터를 스캔함, gold_new_creator_guide가
# 매주 0건임 - 실제로 있었던 버그)은 임계값 알람으로는 안 잡혀서, 추세를 볼 수
# 있는 커스텀 메트릭 4종 + 네이티브 Lambda Duration을 CloudWatch Dashboard
# 하나로 모은다. 그라파나/프로메테우스 없이 기존 인프라(CloudWatch)만으로 처리.
#
# 커스텀 메트릭은 lambda/youtube_api_daily.py(emit_run_metrics)와
# lambda/gold_compute_athena.py(emit_run_metrics)가 각 실행 끝에 직접 보낸다
# (Namespace="Pipeline/PJT" - 기존 AirflowErrorCount/ETLErrorCount와 같은
# namespace. 이 네임스페이스에 아무것도 못 보내고 있었다는 것도 이번에 확인함 -
# PutMetricData 권한이 지금까지 아무 Lambda에도 없었음, 아래에서 추가):
#   - YouTubeApiQuotaUsed      : 이번 실행에서 쓴 쿼터(유닛). 일일 예산 10,000 대비 추세 확인용
#   - BronzeRecordsWritten     : 이번 실행에서 Bronze에 실제로 쓴 레코드 수
#   - AthenaDataScannedBytes   : Gold 집계 쿼리 3개가 스캔한 바이트 총합(쿼리 비용 추세)
#   - GoldNewCreatorGuideCount : 이번 주 gold_new_creator_guide 생성 건수
# ==============================================================================

resource "aws_iam_role_policy" "lambda_cloudwatch_metrics" {
  name = "${local.resource_prefix}-lambda-cloudwatch-metrics-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "PutCustomMetrics"
        Effect = "Allow"
        Action = ["cloudwatch:PutMetricData"]
        # PutMetricData는 리소스 수준 권한을 지원하지 않는 API라 "*"가 맞음
        # (AWS 관리형 정책들도 전부 이렇게 되어 있음).
        Resource = "*"
      }
    ]
  })
}

resource "aws_cloudwatch_dashboard" "pipeline" {
  dashboard_name = "${local.resource_prefix}-pipeline"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "text", x = 0, y = 0, width = 24, height = 1,
        properties = { markdown = "## 파이프라인 수치 모니터링 - 성공/실패 알람 8개로는 안 보이는 추세" }
      },
      {
        type = "metric", x = 0, y = 1, width = 8, height = 6,
        properties = {
          title = "YouTube API 쿼터 사용량 (실행당, 일일 예산 10,000)"
          view  = "timeSeries", stacked = false, region = var.aws_region, period = 14400, stat = "Sum"
          metrics = [["Pipeline/PJT", "YouTubeApiQuotaUsed"]]
        }
      },
      {
        type = "metric", x = 8, y = 1, width = 8, height = 6,
        properties = {
          title = "Bronze 레코드 기록 수 (실행당)"
          view  = "timeSeries", stacked = false, region = var.aws_region, period = 14400, stat = "Sum"
          metrics = [["Pipeline/PJT", "BronzeRecordsWritten"]]
        }
      },
      {
        type = "metric", x = 16, y = 1, width = 8, height = 6,
        properties = {
          title = "Gold 신규 크리에이터 가이드 생성 건수 (주간)"
          view  = "timeSeries", stacked = false, region = var.aws_region, period = 14400, stat = "Sum"
          metrics = [["Pipeline/PJT", "GoldNewCreatorGuideCount"]]
        }
      },
      {
        type = "metric", x = 0, y = 7, width = 8, height = 6,
        properties = {
          title = "Athena 스캔 데이터량 (Gold 집계 쿼리 3개 합, 쿼리 비용 추세)"
          view  = "timeSeries", stacked = false, region = var.aws_region, period = 14400, stat = "Sum"
          metrics = [["Pipeline/PJT", "AthenaDataScannedBytes"]]
        }
      },
      {
        type = "metric", x = 8, y = 7, width = 16, height = 6,
        properties = {
          title = "Lambda 실행 시간 (p50/p99) - 점점 느려지는지 확인"
          view  = "timeSeries", stacked = false, region = var.aws_region, period = 14400
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", "goldline-dev-daily-search-collector", { stat = "p50", label = "daily-search-collector p50" }],
            ["...", { stat = "p99", label = "daily-search-collector p99" }],
            ["AWS/Lambda", "Duration", "FunctionName", "goldline-dev-gold-compute-athena", { stat = "p50", label = "gold-compute-athena p50" }],
            ["...", { stat = "p99", label = "gold-compute-athena p99" }],
            ["AWS/Lambda", "Duration", "FunctionName", "goldline-dev-refresh-dashboard", { stat = "p50", label = "refresh-dashboard p50" }],
            ["...", { stat = "p99", label = "refresh-dashboard p99" }]
          ]
        }
      },
      {
        type = "metric", x = 0, y = 13, width = 12, height = 6,
        properties = {
          title = "Lambda 에러 건수 (알람 8개 중 Lambda 3개분 - 추세로도 확인)"
          view  = "timeSeries", stacked = false, region = var.aws_region, period = 14400, stat = "Sum"
          metrics = [
            ["AWS/Lambda", "Errors", "FunctionName", "goldline-dev-daily-search-collector"],
            ["AWS/Lambda", "Errors", "FunctionName", "goldline-dev-gold-compute-athena"],
            ["AWS/Lambda", "Errors", "FunctionName", "goldline-dev-refresh-dashboard"]
          ]
        }
      },
      {
        type = "metric", x = 12, y = 13, width = 12, height = 6,
        properties = {
          title = "Step Functions 실행 성공/실패 (pipeline-orchestrator)"
          view  = "timeSeries", stacked = false, region = var.aws_region, period = 14400, stat = "Sum"
          # aws_sfn_state_machine.pipeline_orchestrator(다른 팀원이 만들고 계속 바꾸고
          # 있는 infra/pipeline_orchestrator.tf 소유)를 직접 참조하지 않고 ARN을
          # 이름 규칙으로 조립한다 - Terraform 리소스 참조로 걸면 그 파일의 리소스
          # 전체(IAM Role 등)를 이 apply 범위로 끌고 와야 해서, 아직 로컬 state에
          # 없는 그 리소스들이 "AlreadyExists"로 충돌한다(이 세션에서 여러 번 겪은
          # state 미공유 문제와 같은 원인) - ARN 문자열 조립이면 그 문제를 아예 피함.
          metrics = [
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${local.resource_prefix}-pipeline-orchestrator"],
            ["AWS/States", "ExecutionsFailed", "StateMachineArn", "arn:aws:states:${var.aws_region}:${data.aws_caller_identity.current.account_id}:stateMachine:${local.resource_prefix}-pipeline-orchestrator"]
          ]
        }
      }
    ]
  })
}

output "monitoring_dashboard_url" {
  description = "수치 모니터링 대시보드 (CloudWatch 콘솔)"
  value       = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home?region=${var.aws_region}#dashboards:name=${aws_cloudwatch_dashboard.pipeline.dashboard_name}"
}
