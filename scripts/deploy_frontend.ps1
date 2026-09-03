# scripts/deploy_frontend.ps1
# -----------------------------------------------------------------------------
# 프론트엔드 정적 자산(index.html, css/, js/) + 대시보드 데이터(mock/*.json)를
# S3 + CloudFront에 배포한다. infra/frontend.tf 헤더 주석에 적힌 수동 절차를
# 스크립트 하나로 묶은 것 (scripts/deploy_frontend.sh의 PowerShell 버전).
#
# 사용법 (repo 루트에서, PowerShell):
#   .\scripts\deploy_frontend.ps1 assets   # 정적 자산만 배포 (index.html, css/, js/)
#   .\scripts\deploy_frontend.ps1 data     # 대시보드 데이터만 배포 (frontend/mock/*.json)
#   .\scripts\deploy_frontend.ps1 all      # 둘 다 (기본값)
#
# 스크립트 실행이 차단되면(실행 정책 오류):
#   powershell -ExecutionPolicy Bypass -File .\scripts\deploy_frontend.ps1 all
#
# 버킷명 / CloudFront 배포 ID는 infra/ 에서 `terraform output`으로 자동 조회한다
# (그 디렉터리에 terraform state가 있어야 함). 조회가 안 되면 아래처럼 직접
# 지정하고 실행해도 된다:
#   $env:FRONTEND_BUCKET = "goldline-dev-frontend-<account_id>"
#   $env:CF_DISTRIBUTION_ID = "<distribution id>"
#
# 사전 조건: aws configure로 자격증명이 설정돼 있어야 하고, infra/에 terraform
# apply가 이미 된 상태여야 함(버킷/CloudFront가 실제로 존재해야 함).
# -----------------------------------------------------------------------------

param(
    [string]$Mode = "all"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$FrontendDir = Join-Path $ProjectRoot "frontend"
$InfraDir = Join-Path $ProjectRoot "infra"

function Log-Info($msg) { Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Log-Success($msg) { Write-Host "[SUCCESS] $msg" -ForegroundColor Green }
function Log-Err($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red }

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    Log-Err "AWS CLI가 설치되어 있지 않습니다."
    exit 1
}

$AccountId = aws sts get-caller-identity --query Account --output text 2>$null
if (-not $AccountId) {
    Log-Err "AWS 자격증명이 설정되어 있지 않습니다. 'aws configure'를 먼저 실행하세요."
    exit 1
}

$FrontendBucket = $env:FRONTEND_BUCKET
if (-not $FrontendBucket) {
    Push-Location $InfraDir
    $FrontendBucket = terraform output -raw frontend_bucket 2>$null
    Pop-Location
}

$CfDistributionId = $env:CF_DISTRIBUTION_ID
if (-not $CfDistributionId) {
    Push-Location $InfraDir
    $CfDistributionId = terraform output -raw frontend_cloudfront_distribution_id 2>$null
    Pop-Location
}

if (-not $FrontendBucket) {
    Log-Err "FRONTEND_BUCKET을 확인할 수 없습니다. infra/에서 'terraform apply'가 끝났는지 확인하거나,"
    Log-Err '$env:FRONTEND_BUCKET = "goldline-dev-frontend-<account_id>" 로 직접 지정해서 다시 실행하세요.'
    exit 1
}

Log-Info "AWS 계정: $AccountId"
Log-Info "프론트엔드 버킷: $FrontendBucket"
if ($CfDistributionId) {
    Log-Info "CloudFront 배포 ID: $CfDistributionId"
} else {
    Log-Err "CloudFront 배포 ID를 확인할 수 없습니다 - 캐시 무효화는 건너뜁니다."
}

function Deploy-Assets {
    Log-Info "정적 자산 배포 중 (index.html, css/, js/) ..."
    aws s3 sync "$FrontendDir/" "s3://$FrontendBucket/" `
        --delete `
        --exclude "mock/*" `
        --exclude "scripts/*" `
        --exclude "README.md"
    if ($LASTEXITCODE -ne 0) { throw "aws s3 sync (assets) 실패" }
    Log-Success "정적 자산 배포 완료"
}

function Deploy-Data {
    Log-Info "대시보드 데이터 배포 중 (mock/*.json) ..."
    aws s3 sync "$FrontendDir/mock/" "s3://$FrontendBucket/mock/" --delete
    if ($LASTEXITCODE -ne 0) { throw "aws s3 sync (data) 실패" }
    Log-Success "대시보드 데이터 배포 완료"
}

function Invalidate-CloudFront {
    if (-not $CfDistributionId) { return }
    Log-Info "CloudFront 캐시 무효화 요청 중 ..."
    aws cloudfront create-invalidation --distribution-id $CfDistributionId --paths "/*" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "CloudFront 캐시 무효화 실패" }
    Log-Success "캐시 무효화 요청 완료 (전 세계 엣지에 반영되기까지 수 분 걸릴 수 있음)"
}

switch ($Mode) {
    "assets" { Deploy-Assets; Invalidate-CloudFront }
    "data"   { Deploy-Data; Invalidate-CloudFront }
    "all"    { Deploy-Assets; Deploy-Data; Invalidate-CloudFront }
    default {
        Log-Err "알 수 없는 모드: $Mode"
        Write-Host "사용법: .\scripts\deploy_frontend.ps1 [assets|data|all]"
        exit 1
    }
}

Log-Success "완료!"
