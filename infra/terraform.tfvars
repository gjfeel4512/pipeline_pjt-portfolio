# AWS 설정
aws_region = "us-west-2"

# 프로젝트 설정
project_name = "goldline"
environment  = "dev"

# S3 설정
s3_bucket_prefix       = "youtube-data"
s3_versioning_enabled  = true
s3_lifecycle_days      = 30

# CloudWatch 설정
log_retention_days = 30

# 태그
tags = {
  Project     = "Goldline"
  ManagedBy   = "Terraform"
  CreatedDate = "2026-09-01"
  Owner       = "DataEngineering"
  CostCenter  = "Engineering"
}
