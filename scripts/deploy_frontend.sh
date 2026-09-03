#!/bin/bash
# scripts/deploy_frontend.sh
# -----------------------------------------------------------------------------
# 프론트엔드 정적 자산(index.html, css/, js/) + 대시보드 데이터(mock/*.json)를
# S3 + CloudFront에 배포한다. infra/frontend.tf 헤더 주석에 적힌 수동 절차를
# 스크립트 하나로 묶은 것.
#
# 사용법 (repo 루트 또는 scripts/ 어디서나):
#   ./scripts/deploy_frontend.sh assets   # 정적 자산만 배포 (index.html, css/, js/)
#   ./scripts/deploy_frontend.sh data     # 대시보드 데이터만 배포 (frontend/mock/*.json)
#   ./scripts/deploy_frontend.sh all      # 둘 다 (기본값)
#
# 버킷명 / CloudFront 배포 ID는 infra/ 에서 `terraform output`으로 자동 조회한다
# (그 디렉터리에 terraform state가 있어야 함). 다른 환경에서 조회가 안 되면
# FRONTEND_BUCKET / CF_DISTRIBUTION_ID 환경변수로 직접 지정해도 된다.
#
# 사전 조건: aws configure로 자격증명이 설정돼 있어야 하고, infra/에 terraform
# apply가 이미 된 상태여야 함(버킷/CloudFront가 실제로 존재해야 함).
# -----------------------------------------------------------------------------

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
INFRA_DIR="$PROJECT_ROOT/infra"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

if ! command -v aws &> /dev/null; then
    log_error "AWS CLI가 설치되어 있지 않습니다."
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo "")
if [ -z "$ACCOUNT_ID" ]; then
    log_error "AWS 자격증명이 설정되어 있지 않습니다. 'aws configure'를 먼저 실행하세요."
    exit 1
fi

if [ -z "$FRONTEND_BUCKET" ]; then
    FRONTEND_BUCKET=$(cd "$INFRA_DIR" && terraform output -raw frontend_bucket 2>/dev/null || echo "")
fi
if [ -z "$CF_DISTRIBUTION_ID" ]; then
    CF_DISTRIBUTION_ID=$(cd "$INFRA_DIR" && terraform output -raw frontend_cloudfront_distribution_id 2>/dev/null || echo "")
fi

if [ -z "$FRONTEND_BUCKET" ]; then
    log_error "FRONTEND_BUCKET을 확인할 수 없습니다. infra/에서 'terraform apply'가 끝났는지 확인하거나,"
    log_error "FRONTEND_BUCKET=goldline-dev-frontend-<account_id> 환경변수로 직접 지정해서 다시 실행하세요."
    exit 1
fi

log_info "AWS 계정: $ACCOUNT_ID"
log_info "프론트엔드 버킷: $FRONTEND_BUCKET"
if [ -n "$CF_DISTRIBUTION_ID" ]; then
    log_info "CloudFront 배포 ID: $CF_DISTRIBUTION_ID"
else
    log_error "CloudFront 배포 ID를 확인할 수 없습니다 - 캐시 무효화는 건너뜁니다."
fi

deploy_assets() {
    log_info "정적 자산 배포 중 (index.html, css/, js/) ..."
    aws s3 sync "$FRONTEND_DIR/" "s3://$FRONTEND_BUCKET/" \
        --delete \
        --exclude 'mock/*' \
        --exclude 'scripts/*' \
        --exclude 'README.md'
    log_success "정적 자산 배포 완료"
}

deploy_data() {
    log_info "대시보드 데이터 배포 중 (mock/*.json) ..."
    aws s3 sync "$FRONTEND_DIR/mock/" "s3://$FRONTEND_BUCKET/mock/" --delete
    log_success "대시보드 데이터 배포 완료"
}

invalidate() {
    if [ -z "$CF_DISTRIBUTION_ID" ]; then
        return
    fi
    log_info "CloudFront 캐시 무효화 요청 중 ..."
    aws cloudfront create-invalidation --distribution-id "$CF_DISTRIBUTION_ID" --paths '/*' > /dev/null
    log_success "캐시 무효화 요청 완료 (전 세계 엣지에 반영되기까지 수 분 걸릴 수 있음)"
}

case "${1:-all}" in
    assets)
        deploy_assets
        invalidate
        ;;
    data)
        deploy_data
        invalidate
        ;;
    all)
        deploy_assets
        deploy_data
        invalidate
        ;;
    *)
        log_error "알 수 없는 커맨드: $1"
        echo "사용법: $0 [assets|data|all]"
        exit 1
        ;;
esac

log_success "완료!"
