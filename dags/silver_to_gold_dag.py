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
Athena 기반 lambda/gold_compute_athena.py(+ infra/gold_athena.tf, EventBridge 매시간
50분 스케줄)로 대체했다. 이 DAG는 코드/Postgres 데이터를 보존한 채 스케줄만 꺼둔
상태 - 새 경로가 검증되기 전까지의 롤백용. 검증 끝나면 이 DAG와 Postgres/Docker
자체를 완전히 걷어낼 예정("병행 운영 안 함" - trending_rank_tracker나
bronze_to_silver_dag_aws.py 때와 달리 이번엔 최종적으로 하나만 남긴다).
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
# 2026-09-03: Athena 기반 gold_compute_athena Lambda로 대체되어 스케줄 끔
# (None = 수동 트리거만 가능). 새 경로 검증 끝나면 이 DAG 자체를 삭제할 예정.
SCHEDULE_INTERVAL = None

dag = DAG(
    dag_id=DAG_ID,
    default_args=DEFAULT_ARGS,
    schedule_interval=SCHEDULE_INTERVAL,
    description="[대체됨 - gold_compute_athena Lambda 참고] Silver(S3) -> PostgreSQL 적재 -> Gold 집계",
    tags=["etl", "gold", "postgres", "superseded-by-athena"],
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
