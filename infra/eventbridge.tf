# EventBridge 스케줄: 증분 수집 Lambda를 4시간마다(매시 30분) 트리거
resource "aws_cloudwatch_event_rule" "daily_collector_schedule" {
  name                = "${local.resource_prefix}-daily-collector-schedule"
  description         = "YouTube search.list 기반 증분 수집 Lambda 스케줄 (체크포인트로 직전 이후 구간만 검색)"
  schedule_expression = var.daily_collector_schedule_expression

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "daily_collector_target" {
  rule      = aws_cloudwatch_event_rule.daily_collector_schedule.name
  target_id = "${local.resource_prefix}-daily-collector"
  arn       = aws_lambda_function.daily_search_collector.arn
}

# EventBridge가 Lambda를 호출할 수 있도록 Lambda 리소스 정책에 권한 부여
resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.daily_search_collector.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_collector_schedule.arn
}

# EventBridge 스케줄: 트렌딩 순위 추적 Lambda를 매시 15분마다 트리거
# (daily_collector가 매시 30분이라, Silver DAG들끼리 겹치지 않게 15분으로 분리)
resource "aws_cloudwatch_event_rule" "trending_rank_schedule" {
  name                = "${local.resource_prefix}-trending-rank-schedule"
  description         = "트렌딩 순위 추적(chart=mostPopular) Lambda 스케줄"
  schedule_expression = var.trending_rank_schedule_expression

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "trending_rank_target" {
  rule      = aws_cloudwatch_event_rule.trending_rank_schedule.name
  target_id = "${local.resource_prefix}-trending-rank-tracker"
  arn       = aws_lambda_function.trending_rank_tracker.arn
}

resource "aws_lambda_permission" "allow_eventbridge_trending_rank" {
  statement_id  = "AllowExecutionFromEventBridgeTrendingRank"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.trending_rank_tracker.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.trending_rank_schedule.arn
}
