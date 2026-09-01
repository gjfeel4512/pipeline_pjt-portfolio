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
"""
from datetime import timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.utils.dates import days_ago

DAG_ID = "silver_to_gold"
DEFAULT_ARGS = {
    "owner": "airflow",
    "depends_on_past": False,
    "start_date": days_ago(1),
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}
# 일일 Lambda Silver DAG(UTC 00,08,16시 +10분)보다 넉넉히 늦게 실행
SCHEDULE_INTERVAL = "30 0,8,16 * * *"

dag = DAG(
    dag_id=DAG_ID,
    default_args=DEFAULT_ARGS,
    schedule_interval=SCHEDULE_INTERVAL,
    description="Silver(S3) -> PostgreSQL 적재 -> Gold 집계(median/p75)",
    tags=["etl", "gold", "postgres"],
    catchup=False,
    doc_md=__doc__,
)

with dag:
    load_silver = BashOperator(
        task_id="load_silver_to_postgres",
        bash_command=(
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
