# 로컬 변수
locals {
  # 리소스 명명 규칙
  resource_prefix = "${var.project_name}-${var.environment}"

  # S3 버킷 이름 (AWS 계정 ID와 함께 고유성 보장)
  s3_bucket_names = {
    bronze     = "${local.resource_prefix}-bronze-${data.aws_caller_identity.current.account_id}"
    silver     = "${local.resource_prefix}-silver-${data.aws_caller_identity.current.account_id}"
    gold       = "${local.resource_prefix}-gold-${data.aws_caller_identity.current.account_id}"
    terraform  = "${local.resource_prefix}-terraform-state-${data.aws_caller_identity.current.account_id}"
  }

  # CloudWatch 로그 그룹
  log_group_names = {
    airflow    = "/aws/airflow/${local.resource_prefix}"
    etl        = "/aws/etl/${local.resource_prefix}"
    lambda     = "/aws/lambda/${local.resource_prefix}"
  }

  # 공통 태그
  common_tags = merge(
    var.tags,
    {
      Environment = var.environment
      Project     = var.project_name
      Region      = var.aws_region
    }
  )
}

# 현재 AWS 계정 정보
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}
