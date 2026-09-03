# 데이터 수집 · Silver 변환 경로 (2026-09-03 기준)

이전(9/1)에는 서로 다른 두 경로가 공존했지만, 정리되어 지금은 아래 하나의 구조로 통합되어 있습니다.

## 수집 (Bronze)

- `lambda/youtube_api_daily.py` (search.list 기반, EventBridge `30 * * * ? *`)
  → `s3://.../bronze/search/category={slug}/year=/month=/day=/*.jsonl`
- `lambda/trending_rank_tracker.py` (`videos.list(chart=mostPopular)` + `channels.list`, EventBridge `15 * * * ? *`)
  → `s3://.../trending/dt=YYYY-MM-DD/hh=HH/data.json`
- `youtube_api_collector.py` (로컬 1년치 백필, search.list 기반)
  → `outputs/bronze_merged/*.jsonl`
  → `dags/bronze_to_silver_dag_aws.py`의 `push_bronze_to_firehose` 태스크 (파일별 mtime 체크포인트로 신규/변경분만 전송)
  → Kinesis Firehose
  → `s3://.../youtube/bronze/category={slug}/year=/month=/day=/*.gz`

## Silver 변환

- `dags/search_bronze_to_silver_dag.py`, `dags/trending_rank_to_silver_dag.py`, `dags/bronze_to_silver_dag_aws.py` 세 DAG가 각각 위 세 소스를 담당
- 전부 같은 위치(`s3://.../silver/youtube/silver/category=.../`) 밑에 쓰기 때문에, 파일명 접두사로만 출처가 구분됨
- `youtube/silver-rejected/...`에는 검증 실패(오염) 레코드가 별도 보관됨

## Gold

- `dags/silver_to_gold_dag.py` → PostgreSQL(`sql/youtube_pipeline_schema_postgresql.sql`, `sql/compute_gold.sql`) 기준으로만 집계 진행 중
- Glue Catalog / Athena 쪽 Gold 테이블은 아직 없음 (Bronze/Silver는 카탈로그에 있으나 파티션 자동 등록 미구성 상태)

## 이제는 존재하지 않는 것들 (참고용)

- `lambda/daily_mostpopular_collector.py`, `dags/daily_lambda_to_silver_dag.py` — mostPopular 기반 구(舊) 경로.
  `youtube_api_daily.py`(search.list)로 전환하며 삭제(커밋 `6bbe445`), 이후 `trending_rank_tracker.py`가 mostPopular 역할을 별도로 부활시킴
- `dags/bronze_to_silver_dag.py`(비-aws 버전), `transforms/silver_transform.py`(pandas 기반) — 로컬 전용 초기 버전.
  `bronze_to_silver_dag_aws.py`로 기능이 흡수되어 삭제(2026-09-03)
