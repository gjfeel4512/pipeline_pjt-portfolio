/*
  Pipeline-PJT AWS Infrastructure
  
  이 Terraform 코드는 다음을 생성합니다:
  - S3 버킷 (Bronze, Silver, Gold)
  - IAM 역할 및 정책
  - CloudWatch 로그 그룹
  
  사용방법:
    terraform init
    terraform plan
    terraform apply
*/

# 현재 리소스는 다른 .tf 파일에 정의되어 있습니다:
# - provider.tf: AWS provider 설정
# - variables.tf: 변수 정의
# - locals.tf: 로컬 변수
# - s3.tf: S3 버킷
# - iam.tf: IAM 역할 및 정책
# - cloudwatch.tf: CloudWatch 로그
# - outputs.tf: 출력값
