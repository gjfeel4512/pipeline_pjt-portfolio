# 데이터 수집 · Silver 변환 경로 (2026-09-03 기준)

이전(9/1)에는 서로 다른 두 경로가 공존했지만, 정리되어 지금은 아래 하나의 구조로 통합되어 있습니다.

## 수집 (Bronze)

- `lambda/youtube_api_daily.py` (search.list 기반, EventBridge `30 * * * ? *`)
  → `s3://.../bronze/search/category={slug}/year=/month=/day=/*.jsonl`
- `youtube_api_collector.py` (로컬 1년치 백필, search.list 기반) — **일회성 작업, 완료됨(2026-09-03 확인)**
  → `outputs/bronze_merged/*.jsonl` (54개)
  → `dags/bronze_to_silver_dag_aws.py`의 `push_bronze_to_firehose` 태스크 (파일별 mtime 체크포인트로 신규/변경분만 전송)
  → Kinesis Firehose
  → `s3://.../youtube/bronze/category={slug}/year=/month=/day=/*.gz`

## Silver 변환

- `dags/search_bronze_to_silver_dag.py`, `dags/bronze_to_silver_dag_aws.py` 두 DAG가 각각 위 두 소스를 담당
- 전부 같은 위치(`s3://.../silver/youtube/silver/category=.../`) 밑에 쓰기 때문에, 파일명 접두사로만 출처가 구분됨
- `youtube/silver-rejected/...`에는 검증 실패(오염) 레코드가 별도 보관됨
- `bronze_to_silver_dag_aws.py`는 2026-09-03부터 스케줄 비활성화(`schedule_interval=None`,
  `is_paused_upon_creation=True`) — 로컬 백필 54개 파일 전부 Firehose 전송(54/54)·Silver 변환(54/54)
  체크포인트 완료 + S3(`youtube/silver/`, `youtube/silver-rejected/`)에도 실제 존재 확인됨.
  더 처리할 신규 로컬 파일이 없어 자동 실행을 끄고 수동 트리거 전용으로 전환.
  (참고: 이 DAG의 `upload_to_s3_task`가 S3 키를 "실행일(오늘)" 기준으로 만들다 보니, 중간에
  `transforms/backfill_channel_thumbnails.py`가 로컬 파일 mtime을 갱신시켜 전체가 한 번 더
  재처리된 결과 S3에 같은 데이터가 두 날짜 파티션에 중복 존재함 - `fact_video_snapshot`
  upsert가 `(video_id, collected_date)` 기준이라 Postgres/Gold에는 영향 없음, S3 저장공간만
  약간 낭비. 정리는 선택사항.)

## Gold

- 상태(2026-09-03 기준, 마이그레이션 + 라이브 검증 완료 — 아래 "Gold: Athena/Glue 전환" 섹션 참고):
  - `dags/silver_to_gold_dag.py`(PostgreSQL 경로)는 **일시 정지**됨 (`schedule_interval=None`, `is_paused_upon_creation=True`).
    자동 스케줄은 영구히 끄고, 코드/Postgres 데이터는 그대로 보존해서 **수동 트리거 전용 롤백 경로로 계속 유지**한다
    (삭제하지 않기로 결정)
  - `lambda/gold_compute_athena.py` + `infra/gold_athena.tf`가 새 주체: Postgres/RDS 없이 Athena로
    `gold_category_benchmark` / `gold_upload_strategy` / `gold_new_creator_guide` 3종을 직접 산출해 S3 Gold 버킷에 쓴다
  - `gold_video_rank_trend`는 이식 대상에서 제외 — 유일한 소스였던 `trending_rank_tracker.py`가 삭제되어(2026-09-03)
    `trending_rank`가 더 이상 채워지지 않음. Postgres 쪽 테이블/컬럼은 그대로 남아 있고(과거 데이터만 유지),
    Athena/Glue 쪽에는 이 테이블 자체가 없음

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

## Gold: Athena/Glue 전환 (2026-09-03 추가, 배포 + 라이브 검증 완료)

"로컬 의존성 아예 없애자, 작업완료되면 병행작업 안 할거야"라는 결정에 따라, Gold 계층의
PostgreSQL/RDS 의존성을 Athena/Glue로 완전히 대체한다(RDS는 검토 후 기각 — 별도 VPC가 없는
이 프로젝트 구조상 추가 복잡도가 커서 제외). 이미 팀원이 `infra/glue.tf`에 만들어둔 Gold 3종
Glue 카탈로그 테이블(analysis_week 파티션 프로젝션, 이미 Postgres 내보내기용 read-replica로 존재)을
그대로 재사용하고, 쓰는 주체만 Postgres export 스크립트에서 Athena Lambda로 바뀌는 구조다.

### 새로 추가된 파일

- `lambda/gold_compute_athena.py` — `sql/compute_gold.sql`을 Presto/Trino SQL로 이식한 오케스트레이션 Lambda.
  매주 월요일(KST) 기준으로 `analysis_week` 파티션을 purge(S3 DeleteObject)한 다음 `INSERT INTO ... SELECT`로
  다시 채운다 — Athena는 UPSERT가 없으므로 이 "파티션 통째 교체" 방식이 Postgres의
  `ON CONFLICT ... DO UPDATE`와 동일한 멱등성을 낸다. `gold_new_creator_guide`만 Presto `format()` 미지원
  문제를 피하기 위해 SQL로 후보만 뽑고 문구 조립은 Python에서 직접 한다(원래 Postgres template과 동일한 문구).
- `infra/gold_athena.tf` — 위 Lambda + IAM 정책(Athena 쿼리/Glue 조회/Gold 버킷 DeleteObject 추가) +
  EventBridge 스케줄(`cron(50 */4 * * ? *)`, 4시간마다 매시 50분) + CloudWatch 에러 알람.
  최초엔 기존 `silver_to_gold_dag.py`의 옛 "매시 50분"(Silver가 매시간 갱신되던 시절 기준, 지금은 죽은
  `daily_lambda_to_silver_dag` 기준값)을 그대로 가져다 썼는데, 이후 실제 현재 파이프라인 주기를
  다시 확인해서 4시간 주기로 고쳤다 — `infra/variables.tf`의 `daily_collector_schedule_expression`(브론즈 수집)이
  팀원에 의해 `"30 */4 * * ? *"`로, `infra/stepfunctions.tf`의 Silver 변환 스케줄이 `"40 */4 * * ? *"`로
  이미 4시간 주기였다. Gold를 매시간 돌렸다면 Silver가 갱신 안 되는 4번 중 3번은 헛돌 뻔했음.
- `frontend/scripts/export_athena_for_dashboard.py` — `export_pg_for_dashboard.py`(PostgreSQL 버전)의 Athena 대응 스크립트.
  출력 파일명/구조를 기존과 동일하게 맞춰 `frontend/scripts/build_dashboard_data.py`는 수정 없이 그대로 쓰일 수 있다.
  (참고: 대시보드는 상시 서빙 서버가 아니라 개발자가 이 스크립트를 수동으로 돌려
  `outputs/silver_gold_export/*.json`을 갱신하는 로컬/오프라인 워크플로다 — 요청 시 매번 Athena를 쿼리하는 구조가
  아니므로 지연시간은 문제되지 않음).
- `dags/silver_to_gold_dag.py` — 삭제하지 않고 일시 정지만 함(`schedule_interval=None`,
  `is_paused_upon_creation=True`, docstring에 대체 사유 명시). 처음엔 "새 경로 검증되면 완전 삭제" 계획이었는데,
  라이브 검증이 끝난 뒤 **삭제 대신 수동 트리거 전용 롤백 경로로 영구 유지**하기로 결정 변경함
  (`airflow dags trigger silver_to_gold`로 언제든 수동 실행 가능 — Airflow는 paused 상태에서도 수동/API
  트리거는 동작함). 로컬 Postgres/Docker 구성도 함께 유지.

### 배포 + 라이브 검증 완료 (2026-09-03)

`terraform apply`로 `infra/gold_athena.tf`를 배포하고, `aws lambda invoke`로 `gold_compute_athena`를
수동 호출해서 실제 Athena까지 끝까지 돌려봤다. 처음 두 번은 실패했고, 둘 다 코드 자체의 버그였다(Terraform
plan/apply로는 잡히지 않는 종류 — Athena에 실제 쿼리를 던져봐야만 드러남):

1. **타입 불일치(BIGINT vs INT)** — Trino/Presto에서 `COUNT(*)`, `COUNT(DISTINCT ...)`, `day_of_week()`,
   `RANK() OVER (...)`는 전부 BIGINT를 반환하는데, Glue 테이블의 `sample_video_count` /
   `sample_channel_count` / `published_day_of_week` / `strategy_rank` 컬럼은 `int`로 선언되어 있어
   `INSERT INTO`가 컬럼 타입 불일치로 실패. 해당 4곳에 `CAST(... AS INTEGER)` 추가해서 해결.
2. **`INSERT INTO` / `WITH` 절 순서** — `WITH video_analysis AS (...) ... INSERT INTO table SELECT ...`
   순서로 짜여 있었는데, Trino 문법은 `INSERT INTO table WITH ... SELECT ...` 순서를 요구한다
   (`line 94:1: mismatched input 'INSERT'` 에러). `CATEGORY_BENCHMARK_SQL`/`UPLOAD_STRATEGY_SQL` 둘 다
   `INSERT INTO {db}.table`을 WITH절 앞으로 옮겨서 해결.

두 버그를 고친 뒤 세 번째 호출에서 `StatusCode 200`, 에러 없이 성공. S3에도 실제 객체가 써진 것까지 확인함
(`gold_category_benchmark/analysis_week=2026-08-31/`에 376B, `gold_upload_strategy/...`에 35KB).
`gold_new_creator_guide_count`는 0으로 나왔는데, 이건 버그가 아니라 그룹핑 기준(카테고리×구독자군×영상타입×
길이×요일×시간대)이 세밀해서 `new`/`early_growth` 세그먼트가 표본 30건(`MIN_SAMPLE_COUNT`) 기준을 못 채운
것으로 보임 — 백필 데이터가 더 쌓이면 채워질 가능성이 높음. Glue 테이블 3개(`gold_category_benchmark`,
`gold_upload_strategy`, `gold_new_creator_guide`)는 팀원이 이미 실제 AWS에 만들어둔 상태였어서
`terraform import`로 로컬 state에 편입한 뒤 apply함(AlreadyExistsException 발생 → import로 해결,
팀 간 state 미공유가 원인).

### 아직 남은 일

1. 이번 라운드에서 만든 변경 사항은 사용자 요청에 따라 **아직 커밋/푸시되지 않았음** — 배포/검증은
   끝났지만 코드는 로컬에만 반영된 상태.
2. `frontend/scripts/export_athena_for_dashboard.py`가 import하는 `gold_compute_athena.py` 최상단에
   `os.environ["ATHENA_DATABASE"]`처럼 기본값 없는 환경변수 읽기가 있어서, 로컬 PC에서 `--database` 등
   커맨드라인 인자만 주고 이 스크립트를 돌리면 import 시점에 `KeyError`로 죽는다. 대시보드 스크립트를
   실제로 쓰기 전에 손봐야 함(아직 미수정).
3. EventBridge 스케줄(`cron(50 */4 * * ? *)`)로 **자동 실행되는 것까지는 아직 확인 안 됨** — 지금까지는
   `aws lambda invoke`로 수동 호출만 성공했다. 다음 4시간 주기 시점(예: 매시 50분, KST 기준 다음 배수)에
   CloudWatch 로그나 S3 신규 객체로 자동 실행 여부를 한 번 확인해볼 것.
