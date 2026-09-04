# AWS Provider 설정
terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # S3 백엔드 - 2026-09-04부터 활성화. 그동안 팀원마다 로컬 terraform.tfstate가
  # 따로 놀아서(state 공유 안 됨) 이미 존재하는 리소스를 "+create"로 잡아 여러 번
  # AlreadyExistsException을 겪었다(이 세션에서만 frontend/gold_athena/
  # pipeline_orchestrator 리소스 다수를 terraform import로 편입해야 했음).
  # bucket은 locals.tf의 s3_bucket_names.terraform 명명 규칙과 동일하게 미리
  # 예약돼 있던 이름을 그대로 씀. dynamodb_table 대신 Terraform 1.10+ 네이티브
  # S3 잠금(use_lockfile)을 쓴다 - 별도 DynamoDB 테이블 안 만들어도 됨.
  #
  # !! 팀원 안내 !! (이미 S3에 state가 올라가 있는 상태 - 아래 순서를 따를 것,
  # `terraform init`이 "로컬 state를 새 backend로 복사할지" 물어볼 때 실수로
  # "yes" 누르면 이미 올라간 공유 state를 자기 로컬 걸로 덮어쓸 위험이 있음):
  #   1) cd infra
  #   2) mv terraform.tfstate terraform.tfstate.local-backup-<자기이름>  (지우지
  #      말고 백업만 - 혹시 여기 없는 리소스가 있으면 나중에 import 참고용)
  #   3) rm -rf .terraform
  #   4) terraform init   -> 로컬에 옮길 state가 없으니 마이그레이션 질문 자체가
  #      안 뜨고, S3의 공유 state를 그대로 받아옴
  #   5) terraform plan으로 자기 로컬에서만 만들었던 리소스가 "+create"로 잡히면
  #      (아직 공유 state에 없는 것) apply 전에 terraform import로 편입할 것
  backend "s3" {
    bucket       = "goldline-dev-terraform-state-827913617635"
    key          = "goldline/terraform.tfstate"
    region       = "us-west-2"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}
