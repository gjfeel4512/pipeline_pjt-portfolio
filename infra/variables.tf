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
