# 일일 수집 Lambda: search.list 기반 (youtube_api_daily.py)
# - videos.list(chart=mostPopular) 대신 search.list를 써서 이미 뜬 영상만 모이는
#   survivorship bias를 피함 (다이어그램의 "1. 수집(Extract)" 중 Lambda 경로)
# - 15분 하드 타임아웃 안에 전체 검색 작업을 못 끝내면 S3 체크포인트를 저장하고 종료,
#   다음 EventBridge 트리거에서 이어서 진행 (lambda/youtube_api_daily.py 상단 docstring 참고)
data "archive_file" "daily_search_collector" {
  type        = "zip"
  source_file = "${path.module}/../lambda/youtube_api_daily.py"
  output_path = "${path.module}/.daily_search_collector.zip"
}

resource "aws_lambda_function" "daily_search_collector" {
  function_name    = "${local.resource_prefix}-daily-search-collector"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "youtube_api_daily.lambda_handler"
  filename         = data.archive_file.daily_search_collector.output_path
  source_code_hash = data.archive_file.daily_search_collector.output_base64sha256
  timeout          = 900 # Lambda 최대값(15분) - 남은 시간은 코드가 스스로 체크해 조기 종료
  memory_size      = 512

  environment {
    variables = {
      BUCKET_NAME            = aws_s3_bucket.bronze.id
      REGION_CODE            = "KR"
      MAX_RESULTS            = "50"
      YOUTUBE_API_KEYS       = join(",", var.youtube_api_keys)
      TOTAL_DAYS_BACK        = "7"
      TIME_BUDGET_SAFETY_SEC = "60"
    }
  }

  tags = merge(
    local.common_tags,
    {
      Name = "${local.resource_prefix}-daily-search-collector"
    }
  )
}
