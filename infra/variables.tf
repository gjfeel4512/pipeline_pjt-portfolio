# AWS 변수
variable "aws_region" {
  description = "AWS 리전"
  type        = string
  default     = "us-west-2" # "us-west-2"
}

# 프로젝트 설정
variable "project_name" {
  description = "프로젝트명"
  type        = string
  default     = "goldline"
}

variable "environment" {
  description = "환경 (dev, staging, prod)"
  type        = string
  default     = "dev"
}

# S3 설정
variable "s3_bucket_prefix" {
  description = "S3 버킷 프리픽스"
  type        = string
  default     = "youtube-data"
}

variable "s3_versioning_enabled" {
  description = "S3 버전 관리 여부"
  type        = bool
  default     = true
}

variable "s3_lifecycle_days" {
  description = "S3 인telligent-tiering 전환일"
  type        = number
  default     = 30
}

# 태그 설정
variable "tags" {
  description = "공통 태그"
  type        = map(string)
  default = {
    Project     = "Goldline"
    ManagedBy   = "Terraform"
    CreatedDate = "2026-09-01"
  }
}

# CloudWatch 설정
variable "log_retention_days" {
  description = "CloudWatch 로그 보관 기간 (일)"
  type        = number
  default     = 30
}

# Lambda 일일 수집 설정
variable "youtube_api_keys" {
  description = "YouTube Data API 키 목록 (콤마 없이 리스트로, 할당량 소진 시 순서대로 로테이션). secrets.auto.tfvars(gitignore)로 주입할 것 - 절대 커밋 금지."
  type        = list(string)
  sensitive   = true
}

variable "target_category_ids" {
  description = "일일 수집 대상 YouTube videoCategoryId 목록 (영화·애니메이션/자동차·차량/게임/인물·블로그)"
  type        = list(string)
  default     = ["1", "2", "20", "22"]
}

variable "daily_collector_schedule_expression" {
  description = "증분 수집 Lambda EventBridge 스케줄 (UTC 기준 cron). 4시간마다 매시 30분에 실행 (UTC 00:30/04:30/.../20:30 = KST 09:30/13:30/.../05:30). Lambda가 체크포인트(searched_until)로 직전 실행 이후 구간만 검색하므로 텀을 좁혀도 쿼터 부담이 작음"
  type        = string
  # default     = "cron(30 * * * ? *)"       # 매시간
  default = "cron(30 */4 * * ? *)"
}

# SNS 알람 설정
variable "notification_email" {
  description = "파이프라인 알람(SNS)을 받을 이메일. null이면 구독을 생성하지 않음(토픽만 생성)"
  type        = string
  default     = null
}
