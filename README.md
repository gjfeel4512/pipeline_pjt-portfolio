# GOLDLINE — 유튜브 트렌드 데이터 파이프라인

> 원본: https://github.com/jaeyan42/Pipeline_pjt (SK플래닛 AI 데이터 엔지니어 부트캠프 1기, 4인 팀 프로젝트)
> 본인 담당: Silver/Gold Airflow DAG, Terraform 인프라(S3·Glue·IAM·CloudWatch·Step Functions), Gold 파티션 타임존 버그 수정

YouTube 트렌드 영상 데이터를 정기적으로 수집해 Bronze → Silver → Gold 3단계로 가공하고,
AWS Step Functions로 전체 흐름을 완전 자동화한 데이터 파이프라인 프로젝트입니다.

- 수집: Lambda + YouTube Data API v3 (하루 3회, API 키 8개 자동 순환)
- 적재/정제: S3(Bronze) → Airflow/Step Functions로 정제 및 검증(Silver)
- 분석: Athena/Glue 기반 Gold 테이블(카테고리 벤치마크, 업로드 전략, 순위 변동 등)
- 인프라: Terraform으로 S3, Glue, IAM, CloudWatch, Step Functions 전체 코드화

자세한 내용은 `README_SILVER.md`, `README_TERRAFORM.md`, `ARCHITECTURE_NOTE.md`를 참고해 주세요.
