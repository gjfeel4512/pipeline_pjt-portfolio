#!/bin/bash

# Terraform 배포 스크립트
# 사용: ./deploy.sh [init|plan|apply|destroy]

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
INFRA_DIR="$PROJECT_ROOT/infra"

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 함수
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# AWS 계정 확인
check_aws() {
    log_info "AWS 설정 확인 중..."
    
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI가 설치되어 있지 않습니다."
        exit 1
    fi
    
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
    if [ -z "$ACCOUNT_ID" ]; then
        log_error "AWS 자격증명이 설정되어 있지 않습니다."
        echo "다음 명령어를 실행하세요: aws configure"
        exit 1
    fi
    
    REGION=$(aws configure get region || echo "us-west-2")
    log_success "AWS 계정: $ACCOUNT_ID, 리전: $REGION"
}

# Terraform 확인
check_terraform() {
    log_info "Terraform 설정 확인 중..."
    
    if ! command -v terraform &> /dev/null; then
        log_error "Terraform이 설치되어 있지 않습니다."
        exit 1
    fi
    
    TF_VERSION=$(terraform version -json | grep terraform_version | cut -d'"' -f4 || echo "unknown")
    log_success "Terraform 버전: $TF_VERSION"
}

# Terraform Init
terraform_init() {
    log_info "Terraform 초기화 중..."
    
    cd "$INFRA_DIR"
    
    terraform init
    
    log_success "Terraform 초기화 완료"
}

# Terraform Plan
terraform_plan() {
    log_info "Terraform 계획 중..."
    
    cd "$INFRA_DIR"
    
    terraform plan -out=tfplan
    
    log_success "Terraform 계획 완료"
    log_info "계획 결과: tfplan"
}

# Terraform Apply
terraform_apply() {
    log_info "Terraform 적용 중..."
    
    cd "$INFRA_DIR"
    
    if [ ! -f tfplan ]; then
        log_error "tfplan 파일이 없습니다. 먼저 'plan' 커맨드를 실행하세요."
        exit 1
    fi
    
    terraform apply tfplan
    
    log_success "Terraform 적용 완료"
    
    # 환경변수 출력
    log_info "다음 환경변수를 Airflow에 설정하세요:"
    terraform output -raw airflow_env_vars
}

# Terraform Destroy
terraform_destroy() {
    log_warning "이 명령어는 모든 AWS 리소스를 삭제합니다!"
    read -p "정말로 삭제하시겠습니까? (yes/no): " confirm
    
    if [ "$confirm" != "yes" ]; then
        log_info "삭제 취소됨"
        exit 0
    fi
    
    cd "$INFRA_DIR"
    
    terraform destroy
    
    log_success "리소스 삭제 완료"
}

# 메인
main() {
    log_info "Pipeline-PJT AWS 배포 스크립트 시작"
    
    check_aws
    check_terraform
    
    case "${1:-plan}" in
        init)
            terraform_init
            ;;
        plan)
            terraform_plan
            ;;
        apply)
            terraform_plan
            terraform_apply
            ;;
        destroy)
            terraform_destroy
            ;;
        *)
            log_error "알 수 없는 커맨드: $1"
            echo ""
            echo "사용법:"
            echo "  ./scripts/deploy.sh init      - Terraform 초기화"
            echo "  ./scripts/deploy.sh plan      - 변경사항 확인 (기본값)"
            echo "  ./scripts/deploy.sh apply     - 변경사항 적용"
            echo "  ./scripts/deploy.sh destroy   - 리소스 삭제"
            exit 1
            ;;
    esac
    
    log_success "완료!"
}

main "$@"
