#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Airflow DAG: Bronze to Silver Transformation
"""

from datetime import datetime, timedelta
from pathlib import Path
import json
import logging
import sys

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago
from airflow.models import Variable
from airflow.exceptions import AirflowException

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from transforms.silver_transform import transform_jsonl_file

logger = logging.getLogger(__name__)

PIPELINE_ROOT = Variable.get("pipeline_project_root", str(PROJECT_ROOT))
BRONZE_DIR = Path(PIPELINE_ROOT) / Variable.get("bronze_data_dir", "outputs/bronze_merged")
SILVER_DIR = Path(PIPELINE_ROOT) / Variable.get("silver_data_dir", "outputs/silver")
REPORT_DIR = Path(PIPELINE_ROOT) / "reports"

default_args = {
    'owner': 'data-engineering',
    'depends_on_past': False,
    'start_date': days_ago(1),
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

dag = DAG(
    'bronze_to_silver',
    default_args=default_args,
    description='Bronze to Silver ETL Pipeline',
    schedule_interval='0 2 * * *',
    catchup=False,
    tags=['youtube', 'etl'],
)


def validate_bronze_data(**context):
    """Bronze 데이터 검증"""
    logger.info(f"Validating Bronze data in {BRONZE_DIR}")

    if not BRONZE_DIR.exists():
        raise AirflowException(f"Bronze directory not found: {BRONZE_DIR}")

    jsonl_files = list(BRONZE_DIR.glob('*.jsonl'))

    if not jsonl_files:
        raise AirflowException(f"No JSONL files found in {BRONZE_DIR}")

    logger.info(f"Found {len(jsonl_files)} Bronze JSONL files")

    context['task_instance'].xcom_push(
        key='bronze_files',
        value=[str(f) for f in jsonl_files]
    )

    return {'file_count': len(jsonl_files)}


def transform_bronze_to_silver(**context):
    """Bronze 데이터를 Silver로 변환"""
    logger.info("Starting Bronze to Silver transformation")

    ti = context['task_instance']
    bronze_files = ti.xcom_pull(task_ids='validate_bronze', key='bronze_files')

    if not bronze_files:
        raise AirflowException("No Bronze files to process")

    SILVER_DIR.mkdir(parents=True, exist_ok=True)

    transformation_results = []

    for bronze_file in bronze_files:
        bronze_path = Path(bronze_file)
        silver_file = SILVER_DIR / bronze_path.name

        logger.info(f"Processing: {bronze_path.name}")

        result = transform_jsonl_file(bronze_file, str(silver_file))
        transformation_results.append(result)

    ti.xcom_push(key='transformation_results', value=transformation_results)

    total_records = sum(r.get('total_records', 0) for r in transformation_results)
    total_valid = sum(r.get('valid_records', 0) for r in transformation_results)

    logger.info(f"Transformation complete: {total_records} total, {total_valid} valid")

    return {'files_processed': len(transformation_results), 'total_records': total_records}


def generate_transformation_report(**context):
    """변환 결과 리포팅"""
    logger.info("Generating transformation report")

    ti = context['task_instance']
    results = ti.xcom_pull(task_ids='transform_to_silver', key='transformation_results')

    if not results:
        logger.warning("No transformation results to report")
        return

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_file = REPORT_DIR / f"transformation_report_{timestamp}.json"

    report = {
        'timestamp': datetime.now().isoformat(),
        'pipeline': 'bronze_to_silver',
        'status': 'success' if all(r.get('status') == 'success' for r in results) else 'partial_success',
        'total_files': len(results),
        'successful_files': len([r for r in results if r.get('status') == 'success']),
        'summary': {
            'total_records': sum(r.get('total_records', 0) for r in results),
            'total_valid_records': sum(r.get('valid_records', 0) for r in results),
        },
        'details': results,
    }

    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info(f"Report saved: {report_file}")

    return {'report_file': str(report_file)}


def summary_log(**context):
    """최종 요약 로그"""
    logger.info("=" * 60)
    logger.info("Bronze to Silver Transformation Pipeline Summary")
    logger.info("=" * 60)
    logger.info("Pipeline execution completed successfully!")


task_validate = PythonOperator(
    task_id='validate_bronze',
    python_callable=validate_bronze_data,
    provide_context=True,
    dag=dag,
)

task_transform = PythonOperator(
    task_id='transform_to_silver',
    python_callable=transform_bronze_to_silver,
    provide_context=True,
    dag=dag,
)

task_report = PythonOperator(
    task_id='generate_report',
    python_callable=generate_transformation_report,
    provide_context=True,
    dag=dag,
)

task_summary = PythonOperator(
    task_id='summary',
    python_callable=summary_log,
    provide_context=True,
    dag=dag,
)

task_validate >> task_transform >> task_report >> task_summary
