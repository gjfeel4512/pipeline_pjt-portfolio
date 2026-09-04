# 일일 수집 Lambda: search.list 기반 증분 수집 (youtube_api_daily.py)
# - videos.list(chart=mostPopular) 대신 search.list를 써서 이미 뜬 영상만 모이는
#   survivorship bias를 피함 (다이어그램의 "1. 수집(Extract)" 중 Lambda 경로)
# - 영구 체크포인트(searched_until + known_videos)로 증분 발견 + 재스냅샷.
#   known_videos는 게시일 KNOWN_VIDEO_MAX_AGE_DAYS 초과 또는 삭제/비공개 전환 시 자동 제거.
# - Lambda 남은 실행시간이 TIME_BUDGET_SAFETY_SEC 밑으로 떨어지면 배치 처리 중이라도
#   지금까지 모은 것만 저장하고 종료, 다음 EventBridge 트리거가 이어받음
#   (lambda/youtube_api_daily.py 상단 docstring 참고)
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
      BUCKET_NAME              = aws_s3_bucket.bronze.id
      REGION_CODE              = "KR"
      MAX_RESULTS              = "50"
      YOUTUBE_API_KEYS         = join(",", var.youtube_api_keys)
      INITIAL_LOOKBACK_HOURS   = "8"  # 체크포인트 없을 때(콜드 스타트)만 사용
      KNOWN_VIDEO_MAX_AGE_DAYS = "30" # 게시일 이보다 오래되면 known_videos에서 제거
      TIME_BUDGET_SAFETY_SEC   = "60" # 남은 실행시간이 이 밑이면 배치 중이라도 저장 후 종료
      SEARCH_MAX_PAGES         = "2"  # slot당 50건 초과 시 nextPageToken 추적 페이지 수

      # 인기 급상승(chart=mostPopular) 기반 '구독자 대비 떡상' 신규 영상 추적.
      # 끄려면 TRENDING_ENABLED=0. 비율 임계값은 조회수/구독자수 - 5.0이면 조회수가
      # 구독자수의 5배 이상인 영상만 (코드/데이터 보고 조정).
      TRENDING_ENABLED       = "1"
      TRENDING_CATEGORY_IDS  = "1,2,20" # 22(인물_블로그)는 Silver가 reject하므로 제외
      TRENDING_MAX_AGE_DAYS  = "4"      # 게시 4일 이내만 (신선한 떡상만)
      TRENDING_SUB_RATIO_MIN = "5.0"
    }
  }

  tags = merge(
    local.common_tags,
    {
      Name = "${local.resource_prefix}-daily-search-collector"
    }
  )
}
