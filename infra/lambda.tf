# 일일 수집 Lambda: videos.list(mostPopular) + channels.list
# (search.list는 쓰지 않음 - 다이어그램의 "1. 수집(Extract)" 중 Lambda 경로)
data "archive_file" "daily_mostpopular_collector" {
  type        = "zip"
  source_file = "${path.module}/../lambda/daily_mostpopular_collector.py"
  output_path = "${path.module}/.daily_mostpopular_collector.zip"
}

resource "aws_lambda_function" "daily_mostpopular_collector" {
  function_name    = "${local.resource_prefix}-daily-mostpopular-collector"
  role              = aws_iam_role.lambda.arn
  runtime           = "python3.12"
  handler           = "daily_mostpopular_collector.lambda_handler"
  filename          = data.archive_file.daily_mostpopular_collector.output_path
  source_code_hash  = data.archive_file.daily_mostpopular_collector.output_base64sha256
  timeout           = 120
  memory_size       = 256

  environment {
    variables = {
      BUCKET_NAME       = aws_s3_bucket.bronze.id
      CATEGORY_IDS      = join(",", var.target_category_ids)
      REGION_CODE       = "KR"
      MAX_RESULTS       = "50"
      YOUTUBE_API_KEYS  = join(",", var.youtube_api_keys)
    }
  }

  tags = merge(
    local.common_tags,
    {
      Name = "${local.resource_prefix}-daily-mostpopular-collector"
    }
  )
}
