# Pipeline_pjt - AWS Terraform 인프라 (Option A)

YouTube 데이터를 AWS에 저장하기 위한 Terraform 기반 인프라입니다.

## 📋 생성되는 AWS 리소스

### S3 버킷 (3개)
- **Bronze**: 원본 YouTube 데이터 저장
- **Silver**: 정제된 데이터 저장
- **Gold**: 최종 분석 데이터 저장

### 보안
- 모든 버킷에 암호화 활성화 (AES-256)
- Public Access Block 설정
- 버전 관리 활성화
- 자동 생명주기 정책 (오래된 데이터 자동 아카이빙)

### IAM 역할 및 정책
- **Airflow 역할**: S3 읽기/쓰기 + CloudWatch 로그
- **Lambda 역할**: S3 접근 + CloudWatch 로그 (향후 사용)

### CloudWatch
- Airflow 로그 그룹
- ETL 로그 그룹
- Lambda 로그 그룹
- 에러 감지 메트릭

## 📂 폴더 구조

```
Pipeline_pjt/
├── infra/
│   ├── main.tf                 # 메인 파일
│   ├── provider.tf             # AWS provider 설정
│   ├── variables.tf            # 변수 정의
│   ├── locals.tf               # 로컬 변수
│   ├── s3.tf                   # S3 버킷 (★ 핵심)
│   ├── iam.tf                  # IAM 역할/정책
│   ├── cloudwatch.tf           # CloudWatch 로그
│   ├── outputs.tf              # 출력값
│   ├── terraform.tfvars        # 변수 값
│   └── terraform.lock          # 의존성 잠금
│
├── scripts/
│   └── deploy.sh               # 배포 스크립트 (★ 실행 파일)
│
└── README_TERRAFORM.md         # 이 파일
```

## 🚀 빠른 시작

### 1. 사전 요구사항

```bash
# Terraform 설치
# macOS
brew install terraform

# Windows (Chocolatey)
choco install terraform

# Linux
wget https://releases.hashicorp.com/terraform/...

# 확인
terraform version
```

```bash
# AWS CLI 설치 및 설정
aws configure
# AWS Access Key ID: YOUR_KEY
# AWS Secret Access Key: YOUR_SECRET
# Default region: us-west-2
# Default output format: json

# 확인
aws sts get-caller-identity
```

### 2. Terraform 초기화

```bash
cd Pipeline_pjt
chmod +x scripts/deploy.sh
./scripts/deploy.sh init
```

또는 수동으로:
```bash
cd infra
terraform init
```

### 3. 배포 계획 확인

```bash
./scripts/deploy.sh plan
```

출력 예시:
```
Plan: 11 to add, 0 to change, 0 to destroy
```

### 4. 리소스 배포

```bash
./scripts/deploy.sh apply
```

배포 완료 후 출력:
```
Outputs:

airflow_env_vars = 
export AWS_DEFAULT_REGION=us-west-2
export AWS_S3_BRONZE_BUCKET=goldline-dev-bronze-123456789
export AWS_S3_SILVER_BUCKET=goldline-dev-silver-123456789
export AWS_S3_GOLD_BUCKET=goldline-dev-gold-123456789
...
```

## 💾 S3 데이터 업로드

### Bronze에 원본 데이터 업로드

```bash
# 로컬 Bronze 데이터를 S3로 업로드
aws s3 sync outputs/bronze_merged/ \
  s3://$(aws s3 ls | grep bronze | awk '{print $3}')/ \
  --region us-west-2
```

또는 스크립트:
```bash
BRONZE_BUCKET=$(aws s3 ls | grep bronze | awk '{print $3}')
echo "Bronze Bucket: $BRONZE_BUCKET"

aws s3 sync outputs/bronze_merged/ \
  s3://$BRONZE_BUCKET/bronze_merged/ \
  --region us-west-2 \
  --exclude ".gitkeep"
```

## 🔄 Airflow 통합

### 환경변수 설정

배포 후 출력된 환경변수를 Airflow `.env` 파일에 추가:

```bash
# airflow_config/.env에 추가
export AWS_DEFAULT_REGION=us-west-2
export AWS_S3_BRONZE_BUCKET=goldline-dev-bronze-...
export AWS_S3_SILVER_BUCKET=goldline-dev-silver-...
export AWS_S3_GOLD_BUCKET=goldline-dev-gold-...
export AWS_IAM_ROLE_ARN=arn:aws:iam::123456789:role/goldline-dev-airflow-role
```

### DAG에서 S3 사용

```python
import os
import boto3

# 환경변수에서 S3 버킷명 읽기
BRONZE_BUCKET = os.getenv('AWS_S3_BRONZE_BUCKET')
SILVER_BUCKET = os.getenv('AWS_S3_SILVER_BUCKET')

# S3 클라이언트
s3_client = boto3.client('s3', region_name='us-west-2')

# Bronze에서 데이터 다운로드
response = s3_client.get_object(
    Bucket=BRONZE_BUCKET,
    Key='bronze_merged/gaming_2026-08.jsonl'
)

# Silver에 데이터 업로드
s3_client.put_object(
    Bucket=SILVER_BUCKET,
    Key='silver/gaming_2026-08.jsonl',
    Body=transformed_data
)
```

## 📊 비용 추정

| 리소스 | 월별 예상 비용 |
|------|------------|
| S3 Storage (100GB) | $2.30 |
| S3 Request | $0.40 |
| Data Transfer (출수신) | $0.00 (리전 내) |
| CloudWatch Logs (1GB) | $0.50 |
| **합계** | **~$3/월** |

## 🔍 상태 확인

### 배포된 리소스 확인

```bash
cd infra
terraform state list
terraform state show aws_s3_bucket.bronze
```

### S3 버킷 확인

```bash
aws s3 ls
aws s3 ls s3://goldline-dev-bronze-123456789/
```

### CloudWatch 로그 확인

```bash
aws logs describe-log-groups --query 'logGroups[?contains(logGroupName, `goldline`)]'
aws logs tail /aws/airflow/goldline-dev --follow
```

## 🔐 보안 설정

### IAM 권한 최소화

현재 설정:
- ✅ S3 특정 버킷만 접근 가능
- ✅ 필요한 작업만 허용 (GetObject, PutObject)
- ✅ 범용 권한 (AdministratorAccess) 없음

### 버킷 정책 추가

더 강화하려면:
```bash
# IP 제한
# VPC Endpoint 사용
# 특정 IAM 역할만 허용
```

## 🗑️ 리소스 삭제

```bash
# 모든 리소스 삭제 (S3 데이터도 삭제됨)
./scripts/deploy.sh destroy

# 또는 수동으로
cd infra
terraform destroy
```

⚠️ **주의**: 삭제하면 S3의 모든 데이터가 삭제됩니다!

## 📝 Terraform 상태 관리

### 로컬 상태 (현재)
```
infra/terraform.tfstate
infra/terraform.tfstate.backup
```

### 원격 상태 (선택사항)
S3 백엔드로 상태 관리:
```bash
# 1. S3 상태 저장소 버킷 생성 (수동)
aws s3 mb s3://terraform-state-pipeline-123456789

# 2. provider.tf의 backend 섹션 활성화
# backend "s3" {
#   bucket = "terraform-state-pipeline-123456789"
#   key = "goldline/terraform.tfstate"
#   region = "us-west-2"
#   encrypt = true
# }

# 3. 마이그레이션
terraform init
# "Do you want to copy existing state to the new backend?" → yes
```

## 🐛 트러블슈팅

### "error: error reading S3 Bucket"
```
원인: S3 버킷명 중복
해결: terraform.tfvars에서 변수 수정 또는 AWS 계정 확인
```

### "AccessDenied" 에러
```
원인: AWS 권한 부족
확인: aws sts get-caller-identity
필요: S3, IAM, CloudWatch 권한
```

### Terraform state 손상
```
복구:
cd infra
terraform state backup
rm terraform.tfstate
terraform refresh
```

## 📚 다음 단계

### Option B로 확장 (실시간 처리)
- AWS Kinesis 추가
- Lambda 함수 추가
- Glue 작업 추가

### 모니터링 강화
- CloudWatch 대시보드
- SNS 알림
- 비용 알림

### 백업 전략
- S3 Cross-Region Replication
- Glacier 아카이빙
- 정기 스냅샷

## 📖 참고자료

- [Terraform 공식 문서](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [AWS S3 Terraform](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket)
- [Terraform Best Practices](https://cloud.google.com/docs/terraform/best-practices)

---

**생성일:** 2026-09-01  
**상태:** 완성 (Option A)  
**다음:** Option B 구성
