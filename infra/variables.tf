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
  default     = "pipeline-pjt"
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
    Project     = "Pipeline-PJT"
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
  description = "일일 수집 Lambda EventBridge 스케줄 (UTC 기준 cron). 기본값: KST 09시/17시/01시 = UTC 00,08,16시"
  type        = string
  default     = "cron(0 0,8,16 * * ? *)"
}

# SNS 알람 설정
variable "notification_email" {
  description = "파이프라인 알람(SNS)을 받을 이메일. null이면 구독을 생성하지 않음(토픽만 생성)"
  type        = string
  default     = null
}
