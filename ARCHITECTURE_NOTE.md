# ⚠️ 수집·Silver 경로 두 개 공존 중 (2026-09-01)

지금 이 repo에는 서로 다른 두 가지 수집/Silver 변환 경로가 동시에 존재합니다.
아직 하나로 합치지 않기로 팀에서 결정했고, **나중에 다시 논의해서 정할 예정**입니다.
헷갈리지 않도록 여기에 남겨둡니다.

## 경로 A — Lambda + Airflow (Goldline 팀 기본 설계)

```
Lambda(daily_mostpopular_collector) → s3://.../bronze/daily/dt=YYYY-MM-DD/hh=HH/data.json
    → dags/daily_lambda_to_silver_dag.py → s3://.../silver/youtube/silver/category=.../year=.../
```

- `videos.list(mostPopular)` + `channels.list` 기반, `search.list` 안 씀
- 수집: EventBridge 스케줄(하루 3회)로 완전 자동
- 최종 조회: PostgreSQL (`sql/youtube_pipeline_schema_postgresql.sql`)

## 경로 B — search.list + Firehose + Glue/Athena

```
youtube_api_collector.py(search.list 기반) → outputs/bronze_merged/*.jsonl
    → transforms/push_to_firehose.py 또는 dags/bronze_to_silver_dag_aws.py의
      push_bronze_to_firehose 태스크 → Kinesis Firehose → s3://.../bronze/youtube/bronze/...
    → dags/bronze_to_silver_dag_aws.py → s3://.../silver/youtube/silver/...
```

- Glue Catalog(`bronze_youtube`, `silver_youtube`, `silver_youtube_rejected`)로 Athena 조회 목적
- ⚠️ 아직 파티션 자동 등록(Glue Crawler / MSCK REPAIR) 미구성 — 지금 상태로는 Athena 조회 시 0건

## 알아둘 것

- 두 경로 모두 같은 S3 버킷(`.../silver/youtube/silver/...`) 밑에 쓰기 때문에, 파일명(`silver_daily_*` vs 다른 접두사)으로만 구분됩니다.
- Gold 레이어(집계·처방 카드)는 **PostgreSQL 기준으로만** 진행 중입니다. Athena 쪽 Gold 테이블은 아직 없습니다.
- 최종적으로 하나만 남기기로 결정되면, 진 쪽 코드는 삭제하거나 `archive/` 브랜치로 옮기고 이 문서도 지웁니다.
