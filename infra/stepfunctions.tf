# ============================================================================
# Step Functions로 옮긴 search_bronze_to_silver 파이프라인
#
# dags/search_bronze_to_silver_dag.py와 완전히 동일한 일(오늘 KST 파티션의
# bronze/search/... -> Silver 변환)을, 로컬 Airflow 없이 AWS 안에서만 돈다.
# Airflow DAG는 그대로 두고(팀 결정으로 삭제하지 않음) 이 상태머신을 병렬로 운영한다 -
# 둘 다 같은 S3 Silver 경로에 같은 파일명 규칙으로 쓰기 때문에, 같은 신규 Bronze
# 오브젝트를 이미 한쪽이 처리했어도 다른 쪽이 다시 처리하면 그냥 같은 내용으로
# 덮어써질 뿐 데이터가 깨지진 않는다(멱등적 변환 - PutObject는 매번 같은 키에 같은
# 내용을 씀).
#
# 1) list_bronze_search (Lambda)   : 오늘 파티션의 신규 .jsonl 키 목록 조회
# 2) HasKeys (Choice)               : 키가 없으면 바로 종료
# 3) TransformEachKey (Map, 병렬)   : 키마다 transform_search_to_silver (Lambda) 호출
# ============================================================================

# ---- Lambda 1: Bronze 오브젝트 키 목록 조회 ----
data "archive_file" "stepfn_list_bronze_search" {
  type        = "zip"
  source_file = "${path.module}/../lambda/stepfn_list_bronze_search.py"
  output_path = "${path.module}/.stepfn_list_bronze_search.zip"
}

resource "aws_lambda_function" "stepfn_list_bronze_search" {
  function_name    = "${local.resource_prefix}-stepfn-list-bronze-search"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "stepfn_list_bronze_search.lambda_handler"
  filename         = data.archive_file.stepfn_list_bronze_search.output_path
  source_code_hash = data.archive_file.stepfn_list_bronze_search.output_base64sha256
  timeout          = 30
  memory_size      = 256

  environment {
    variables = {
      BUCKET_NAME = aws_s3_bucket.bronze.id
    }
  }

  tags = merge(local.common_tags, { Name = "${local.resource_prefix}-stepfn-list-bronze-search" })
}

# ---- Lambda 2: 키 하나를 Silver로 변환 (Map 상태가 병렬 호출) ----
data "archive_file" "stepfn_transform_search_silver" {
  type        = "zip"
  source_file = "${path.module}/../lambda/stepfn_transform_search_silver.py"
  output_path = "${path.module}/.stepfn_transform_search_silver.zip"
}

resource "aws_lambda_function" "stepfn_transform_search_silver" {
  function_name    = "${local.resource_prefix}-stepfn-transform-search-silver"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "stepfn_transform_search_silver.lambda_handler"
  filename         = data.archive_file.stepfn_transform_search_silver.output_path
  source_code_hash = data.archive_file.stepfn_transform_search_silver.output_base64sha256
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      BRONZE_BUCKET_NAME = aws_s3_bucket.bronze.id
      SILVER_BUCKET_NAME = aws_s3_bucket.silver.id
    }
  }

  tags = merge(local.common_tags, { Name = "${local.resource_prefix}-stepfn-transform-search-silver" })
}

# ---- Step Functions 실행 로그 ----
resource "aws_cloudwatch_log_group" "search_to_silver_sfn" {
  name              = "/aws/vendedlogs/states/${local.resource_prefix}-search-to-silver"
  retention_in_days = 14

  tags = local.common_tags
}

# ---- IAM 역할: Step Functions용 (Lambda 호출 + 로그 기록) ----
resource "aws_iam_role" "step_functions" {
  name = "${local.resource_prefix}-stepfn-role"

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

resource "aws_iam_role_policy" "step_functions_invoke_lambda" {
  name = "${local.resource_prefix}-stepfn-invoke-lambda"
  role = aws_iam_role.step_functions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeLambda"
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = [
          aws_lambda_function.stepfn_list_bronze_search.arn,
          aws_lambda_function.stepfn_transform_search_silver.arn,
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

# ---- 상태머신 정의 (ASL) ----
resource "aws_sfn_state_machine" "search_to_silver" {
  name     = "${local.resource_prefix}-search-to-silver"
  role_arn = aws_iam_role.step_functions.arn
  type     = "STANDARD"

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.search_to_silver_sfn.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = jsonencode({
    Comment = "search_bronze_to_silver_dag.py와 동일한 로직을 Airflow/로컬 없이 실행 (병렬 운영)"
    StartAt = "ListBronzeSearchObjects"
    States = {
      ListBronzeSearchObjects = {
        Type     = "Task"
        Resource = aws_lambda_function.stepfn_list_bronze_search.arn
        ResultPath = "$.list_result"
        Next     = "HasKeys"
      }
      HasKeys = {
        Type = "Choice"
        Choices = [
          {
            Variable       = "$.list_result.keys[0]"
            IsPresent      = true
            Next           = "TransformEachKey"
          }
        ]
        Default = "NoNewData"
      }
      NoNewData = {
        Type    = "Pass"
        Result  = { message = "신규/변경된 bronze/search 오브젝트 없음 - 스킵" }
        End     = true
      }
      TransformEachKey = {
        Type           = "Map"
        ItemsPath      = "$.list_result.keys"
        MaxConcurrency = 4
        ItemSelector = {
          "key.$" = "$$.Map.Item.Value"
        }
        ItemProcessor = {
          ProcessorConfig = { Mode = "INLINE" }
          StartAt         = "TransformOneKey"
          States = {
            TransformOneKey = {
              Type     = "Task"
              Resource = aws_lambda_function.stepfn_transform_search_silver.arn
              Retry = [
                {
                  ErrorEquals     = ["States.TaskFailed", "Lambda.TooManyRequestsException"]
                  IntervalSeconds = 5
                  MaxAttempts     = 2
                  BackoffRate     = 2.0
                }
              ]
              End = true
            }
          }
        }
        End = true
      }
    }
  })

  tags = local.common_tags
}

# ---- EventBridge 스케줄: daily_search_collector Lambda(4시간마다 매시 30분)보다
#      10분 늦게 실행해서 새로 생긴 Bronze 데이터를 처리 (Airflow DAG와 동일한 오프셋 관례) ----
resource "aws_cloudwatch_event_rule" "search_to_silver_sfn_schedule" {
  name                = "${local.resource_prefix}-search-to-silver-sfn-schedule"
  description         = "search_bronze_to_silver Step Functions 파이프라인 스케줄 (Airflow DAG와 병렬 운영)"
  schedule_expression = "cron(40 */4 * * ? *)"

  tags = local.common_tags
}

resource "aws_iam_role" "eventbridge_sfn" {
  name = "${local.resource_prefix}-eventbridge-sfn-role"

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

resource "aws_iam_role_policy" "eventbridge_start_sfn" {
  name = "${local.resource_prefix}-eventbridge-start-sfn"
  role = aws_iam_role.eventbridge_sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = aws_sfn_state_machine.search_to_silver.arn
      }
    ]
  })
}

resource "aws_cloudwatch_event_target" "search_to_silver_sfn_target" {
  rule      = aws_cloudwatch_event_rule.search_to_silver_sfn_schedule.name
  target_id = "${local.resource_prefix}-search-to-silver-sfn"
  arn       = aws_sfn_state_machine.search_to_silver.arn
  role_arn  = aws_iam_role.eventbridge_sfn.arn
}
