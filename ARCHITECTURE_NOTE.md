# 데이터 수집 · Silver 변환 경로 (2026-09-03 기준)

이전(9/1)에는 서로 다른 두 경로가 공존했지만, 정리되어 지금은 아래 하나의 구조로 통합되어 있습니다.

## 수집 (Bronze)

- `lambda/youtube_api_daily.py` (search.list 기반, EventBridge `30 * * * ? *`)
  → `s3://.../bronze/search/category={slug}/year=/month=/day=/*.jsonl`
- `youtube_api_collector.py` (로컬 1년치 백필, search.list 기반)
  → `outputs/bronze_merged/*.jsonl`
  → `dags/bronze_to_silver_dag_aws.py`의 `push_bronze_to_firehose` 태스크 (파일별 mtime 체크포인트로 신규/변경분만 전송)
  → Kinesis Firehose
  → `s3://.../youtube/bronze/category={slug}/year=/month=/day=/*.gz`

## Silver 변환

- `dags/search_bronze_to_silver_dag.py`, `dags/bronze_to_silver_dag_aws.py` 두 DAG가 각각 위 두 소스를 담당
- 전부 같은 위치(`s3://.../silver/youtube/silver/category=.../`) 밑에 쓰기 때문에, 파일명 접두사로만 출처가 구분됨
- `youtube/silver-rejected/...`에는 검증 실패(오염) 레코드가 별도 보관됨

## Gold

- `dags/silver_to_gold_dag.py` → PostgreSQL(`sql/youtube_pipeline_schema_postgresql.sql`, `sql/compute_gold.sql`) 기준으로만 집계 진행 중
- Glue Catalog / Athena 쪽 Gold 테이블은 아직 없음 (Bronze/Silver는 카탈로그에 있으나 파티션 자동 등록 미구성 상태)
- `gold_video_rank_trend`는 `fact_video_snapshot.trending_rank IS NOT NULL`인 레코드만 집계하는데,
  이 필드를 채우던 유일한 소스가 `trending_rank_tracker.py`였다. 해당 Lambda를 삭제(2026-09-03)했으므로
  이후로는 `trending_rank`가 항상 NULL만 들어오고, `gold_video_rank_trend`는 삭제 시점까지 쌓인
  과거 데이터에서 더 이상 갱신되지 않는다(테이블/컬럼 자체는 그대로 남아 있음. 필요하면 별도로 정리 필요).

## 이제는 존재하지 않는 것들 (참고용)

- `lambda/daily_mostpopular_collector.py`, `dags/daily_lambda_to_silver_dag.py` — mostPopular 기반 구(舊) 경로.
  `youtube_api_daily.py`(search.list)로 전환하며 삭제(커밋 `6bbe445`), 이후 `trending_rank_tracker.py`가 mostPopular 역할을 별도로 부활시킴
- `dags/bronze_to_silver_dag.py`(비-aws 버전), `transforms/silver_transform.py`(pandas 기반) — 로컬 전용 초기 버전.
  `bronze_to_silver_dag_aws.py`로 기능이 흡수되어 삭제(2026-09-03)
- `lambda/trending_rank_tracker.py`, `dags/trending_rank_to_silver_dag.py` — 트렌딩 순위 추적(mostPopular) 경로.
  더 이상 사용하지 않기로 하여 삭제(2026-09-03). 함께 제거된 인프라: `infra/lambda.tf`의 Lambda 정의,
  `infra/eventbridge.tf`의 스케줄/타겟/권한, `infra/cloudwatch.tf`의 에러 알람, `infra/variables.tf`의
  `trending_rank_schedule_expression` 변수. `terraform apply`로 실제 AWS 리소스(Lambda, EventBridge 규칙,
  CloudWatch 알람)까지 제거해야 완전히 정리됨(아직 미적용). Gold의 `gold_video_rank_trend` 테이블은
  이 데이터에만 의존했으므로 함께 영향받음 — 위 Gold 섹션 참고.

## Step Functions 병렬 경로 (2026-09-03 추가, Airflow는 삭제하지 않음)

`search_bronze_to_silver_dag.py`(Lambda→S3→S3, 로컬 의존 없음)와 동일한 로직을
로컬 Airflow 없이도 돌리기 위해 AWS Step Functions 상태머신을 **추가**했다.
기존 Airflow DAG는 그대로 두고 병렬로 운영한다 — 둘 다 같은 Silver 경로에
같은 파일명 규칙으로 멱등적(idempotent)으로 쓰기 때문에 중복 실행돼도 데이터가
깨지지 않는다(PutObject가 같은 키에 같은 내용을 덮어쓸 뿐).

- `lambda/stepfn_list_bronze_search.py` — 오늘(KST) 파티션의 bronze/search/ 오브젝트 키 목록 조회
- `lambda/stepfn_transform_search_silver.py` — 키 하나를 Silver로 변환(Map 상태가 병렬 호출)
- `infra/stepfunctions.tf` — 상태머신 정의 + EventBridge 스케줄(`cron(40 */4 * * ? *)`, Lambda 수집 스케줄 10분 뒤)
- 인프라 배포: `terraform apply`로 반영 필요 (아직 미배포)
