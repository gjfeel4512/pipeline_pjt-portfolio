"""
Silver -> PostgreSQL -> Gold 집계 DAG

1) transforms/load_silver_to_postgres.py: S3 Silver(youtube/silver/) -> PostgreSQL
   (dim_date/dim_channel/fact_video_snapshot upsert)
2) sql/compute_gold.sql: fact_video_snapshot -> gold_category_benchmark /
   gold_upload_strategy / gold_new_creator_guide (median/p75 집계, 결정적 계산 -
   LLM/Bedrock은 이 결과를 문장으로 바꾸는 역할만 하고 숫자 계산은 절대 하지 않음)
3) transforms/export_gold_to_s3.py: PostgreSQL의 gold_* 테이블 -> S3 Gold 버킷에
   JSON으로 내보냄. PostgreSQL이 실제 조회 대상이고 S3는 원본 보관/재현용 사본
   (다이어그램의 "s3://bucket/gold/ <-> PostgreSQL" 양방향 관계 그대로)

Silver 수집 DAG(daily_lambda_to_silver_dag.py, bronze_to_silver_dag_aws.py)들이
S3 Silver에 데이터를 쓴 뒤에 실행되어야 하므로 그보다 늦은 시각으로 스케줄한다.
(daily_lambda_to_silver_dag가 매시간 40분으로 바뀜에 따라 이 DAG도 매시간 50분으로 동기화함)

2026-09-03: Postgres/RDS 없이 완전 서버리스로 가기로 하여, 이 DAG(PostgreSQL 경로)를
Athena 기반 lambda/gold_compute_athena.py(+ infra/gold_athena.tf, EventBridge 4시간마다
매시 50분 스케줄)로 대체했다. 새 경로는 실제 배포 + 수동 invoke로 라이브 검증까지
끝났다(S3에 gold_category_benchmark/gold_upload_strategy 데이터 확인).

이 DAG는 삭제하지 않는다 - 자동 스케줄은 계속 꺼둔 채(schedule_interval=None,
is_paused_upon_creation=True) 코드/Postgres 데이터를 그대로 보존해서, 필요할 때
`airflow dags trigger silver_to_gold`처럼 수동으로만 돌릴 수 있게 남겨둔다(Airflow는
paused 상태에서도 수동/API 트리거는 그대로 동작함). trending_rank_tracker나
bronze_to_silver_dag_aws.py와 마찬가지로 "완전 삭제"가 아니라 "수동 백업 경로 유지"로
결정함.
"""
from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.utils.dates import days_ago

DAG_ID = "silver_to_gold"
LOCAL_TZ = pendulum.timezone('Asia/Seoul')
DEFAULT_ARGS = {
    "owner": "airflow",
    "depends_on_past": False,
    "start_date": pendulum.datetime(2024, 1, 1, tz=LOCAL_TZ),
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}
# 일일 Lambda Silver DAG(UTC 00,08,16시 +10분)보다 넉넉히 늦게 실행
# SCHEDULE_INTERVAL = "30 0,8,16 * * *"
# daily_lambda_to_silver_dag(매시간 40분)보다 10분 늦게 실행
# 2026-09-03: Athena 기반 gold_compute_athena Lambda로 대체(+ 라이브 검증 완료)되어
# 자동 스케줄은 영구히 끔(None = 수동 트리거만 가능). 이 DAG는 삭제하지 않고
# 수동 롤백 경로로 유지한다 - 위 docstring 참고.
SCHEDULE_INTERVAL = None

dag = DAG(
    dag_id=DAG_ID,
    default_args=DEFAULT_ARGS,
    schedule_interval=SCHEDULE_INTERVAL,
    description="[대체됨 - gold_compute_athena Lambda 참고, 수동 롤백용으로 유지] Silver(S3) -> PostgreSQL 적재 -> Gold 집계",
    tags=["etl", "gold", "postgres", "superseded-by-athena", "manual-rollback-only"],
    catchup=False,
    is_paused_upon_creation=True,
    doc_md=__doc__,
)

with dag:
    load_silver = BashOperator(
        task_id="load_silver_to_postgres",
        bash_command=(
            "PGHOST={{ var.value.get('pg_host', 'postgres') }} "
            "PGPORT=5432 PGDATABASE=airflow PGUSER=airflow PGPASSWORD=airflow "
            "python {{ var.value.get('pipeline_project_root', '/pipeline') }}/transforms/load_silver_to_postgres.py "
            "--s3-prefix youtube/silver/ "
            "--bucket {{ var.value.get('aws_s3_silver_bucket', '') }}"
        ),
    )

    compute_gold = BashOperator(
        task_id="compute_gold",
        bash_command=(
            "PGHOST={{ var.value.get('pg_host', 'postgres') }} "
            "PGPORT=5432 PGDATABASE=airflow PGUSER=airflow PGPASSWORD=airflow "
            "psql -f {{ var.value.get('pipeline_project_root', '/pipeline') }}/sql/compute_gold.sql"
        ),
    )

    export_gold = BashOperator(
        task_id="export_gold_to_s3",
        bash_command=(
            "PGHOST={{ var.value.get('pg_host', 'postgres') }} "
            "PGPORT=5432 PGDATABASE=airflow PGUSER=airflow PGPASSWORD=airflow "
            "python {{ var.value.get('pipeline_project_root', '/pipeline') }}/transforms/export_gold_to_s3.py "
            "--bucket {{ var.value.get('aws_s3_gold_bucket', '') }}"
        ),
    )

    load_silver >> compute_gold >> export_gold
