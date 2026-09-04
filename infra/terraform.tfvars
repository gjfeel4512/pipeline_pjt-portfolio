# AWS 설정
aws_region = "us-west-2"

# 프로젝트 설정
project_name = "goldline"
environment  = "dev"

# S3 설정
s3_bucket_prefix      = "youtube-data"
s3_versioning_enabled = true
s3_lifecycle_days     = 30

# CloudWatch 설정
log_retention_days = 30

# 알람 수신 이메일 - apply하면 SNS 구독이 생성되고, 받은 확인 메일의 링크를 눌러야
# 알람이 실제로 전달됨 (pipeline-orchestrator-failed / refresh-dashboard-errors).
notification_emails = ["jaeyoung5751@gmail.com", "gjfeel4512@gmail.com", "yoonmo335@gmail.com"]

# 태그
tags = {
  Project     = "Goldline"
  ManagedBy   = "Terraform"
  CreatedDate = "2026-09-01"
  Owner       = "DataEngineering"
  CostCenter  = "Engineering"
}
