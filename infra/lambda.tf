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
  timeout          = 900 # Lambda 최대값(15분). 증분 방식이라 보통 수 분 내 종료
  memory_size      = 512

  # 동시 실행 1개로 제한: EventBridge 중복 전달/실행 겹침 시 체크포인트 경합(마지막
  # 쓰기 승리로 known_videos 일부 유실) 방지. 4시간 텀이라 큐잉될 일도 없음.
  reserved_concurrent_executions = 1

  environment {
    variables = {
      BUCKET_NAME            = aws_s3_bucket.bronze.id
      REGION_CODE            = "KR"
      MAX_RESULTS            = "50"
      YOUTUBE_API_KEYS       = join(",", var.youtube_api_keys)
      INITIAL_LOOKBACK_HOURS = "8" # 체크포인트 없을 때(콜드 스타트)만 사용
    }
  }

  tags = merge(
    local.common_tags,
    {
      Name = "${local.resource_prefix}-daily-search-collector"
    }
  )
}

# 트렌딩 순위 추적 Lambda: videos.list(chart=mostPopular) + channels.list
# - 신규 영상 발견용이 아님(그건 daily_search_collector 담당) - 이미 차트 상위권인
#   영상들의 순위/조회수 변화 추적 전용 (gold_video_rank_trend의 유일한 데이터 소스,
#   lambda/trending_rank_tracker.py 상단 docstring 참고)
# - search.list(100유닛)를 안 써서 저비용(mostPopular=1유닛, channels.list=1유닛)
data "archive_file" "trending_rank_tracker" {
  type        = "zip"
  source_file = "${path.module}/../lambda/trending_rank_tracker.py"
  output_path = "${path.module}/.trending_rank_tracker.zip"
}

resource "aws_lambda_function" "trending_rank_tracker" {
  function_name    = "${local.resource_prefix}-trending-rank-tracker"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "trending_rank_tracker.lambda_handler"
  filename         = data.archive_file.trending_rank_tracker.output_path
  source_code_hash = data.archive_file.trending_rank_tracker.output_base64sha256
  timeout          = 120
  memory_size      = 256

  environment {
    variables = {
      BUCKET_NAME      = aws_s3_bucket.bronze.id
      CATEGORY_IDS     = join(",", var.target_category_ids)
      REGION_CODE      = "KR"
      MAX_RESULTS      = "50"
      YOUTUBE_API_KEYS = join(",", var.youtube_api_keys)
    }
  }

  tags = merge(
    local.common_tags,
    {
      Name = "${local.resource_prefix}-trending-rank-tracker"
    }
  )
}
