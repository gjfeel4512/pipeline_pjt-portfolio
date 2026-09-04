# SNS: 파이프라인 알람 (성공/실패, 용량 초과, 에러 로그 감지)
resource "aws_sns_topic" "pipeline_alerts" {
  name = "${local.resource_prefix}-alerts"

  tags = local.common_tags
}

resource "aws_sns_topic_subscription" "pipeline_alerts_email" {
  for_each = toset(var.notification_emails)

  topic_arn = aws_sns_topic.pipeline_alerts.arn
  protocol  = "email"
  endpoint  = each.value
}
