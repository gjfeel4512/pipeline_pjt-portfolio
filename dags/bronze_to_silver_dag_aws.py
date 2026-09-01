"""
Bronze to Silver ETL DAG with AWS S3 Integration
Transforms raw YouTube data from Bronze to Silver layer and uploads to S3

Architecture:
- Bronze Layer: Raw data from data collection (local/S3)
- Silver Layer: Cleaned, validated, transformed data
- Storage: Local (for development) + AWS S3 (for production)
"""

from datetime import datetime, timedelta
from pathlib import Path
import json
import logging
import os

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
from airflow.utils.dates import days_ago
from airflow.models import Variable
from airflow.exceptions import AirflowException

import boto3
from botocore.exceptions import ClientError

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

AIRFLOW_HOME = os.getenv('AIRFLOW_HOME', '/pipeline')
BRONZE_DIR = f'{AIRFLOW_HOME}/outputs/bronze_merged'
SILVER_DIR = f'{AIRFLOW_HOME}/outputs/silver'
REPORTS_DIR = f'{AIRFLOW_HOME}/reports'

# AWS Configuration
AWS_REGION = os.getenv('AWS_DEFAULT_REGION', 'us-west-2')
AWS_S3_SILVER_BUCKET = os.getenv('AWS_S3_SILVER_BUCKET')
AWS_S3_BRONZE_BUCKET = os.getenv('AWS_S3_BRONZE_BUCKET')
AWS_CLOUDWATCH_LOG_GROUP = os.getenv('AWS_CLOUDWATCH_LOG_GROUP', '/aws/airflow/pipeline-pjt-dev')

# Firehose Configuration (Bronze 자동 적재)
FIREHOSE_STREAM_NAME = os.getenv('FIREHOSE_STREAM_NAME', 'pipeline-pjt-dev-bronze-stream')
CATEGORY_MAP = {
    'film_animation': 'film_animation',
    'autos_vehicles': 'autos_vehicles',
    'gaming': 'gaming',
    'people_blogs': 'people_blogs',
}

# DAG Configuration
DAG_ID = 'bronze_to_silver_with_s3'
DEFAULT_ARGS = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': days_ago(1),
    'email': ['airflow@pipeline.local'],
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

SCHEDULE_INTERVAL = '0 2 * * *'  # Daily at 02:00 KST

# ============================================================================
# AWS Helper Functions
# ============================================================================

def get_s3_client():
    """Initialize S3 client with current AWS credentials"""
    try:
        client = boto3.client('s3', region_name=AWS_REGION)
        # Test connection
        client.head_bucket(Bucket=AWS_S3_SILVER_BUCKET)
        logger.info(f"✓ AWS S3 connection successful - Bucket: {AWS_S3_SILVER_BUCKET}")
        return client
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == '404':
            raise AirflowException(f"S3 bucket not found: {AWS_S3_SILVER_BUCKET}")
        elif error_code == '403':
            raise AirflowException(f"Access denied to S3 bucket: {AWS_S3_SILVER_BUCKET}")
        else:
            raise AirflowException(f"AWS S3 error: {str(e)}")

def upload_to_s3(file_path, s3_key, bucket=None):
    """Upload file to AWS S3"""
    if bucket is None:
        bucket = AWS_S3_SILVER_BUCKET

    try:
        s3_client = get_s3_client()
        file_size = os.path.getsize(file_path)

        logger.info(f"Uploading {file_path} to s3://{bucket}/{s3_key} ({file_size} bytes)")

        s3_client.upload_file(file_path, bucket, s3_key)

        logger.info(f"✓ Successfully uploaded to S3: s3://{bucket}/{s3_key}")
        return f"s3://{bucket}/{s3_key}"
    except Exception as e:
        logger.error(f"✗ Failed to upload to S3: {str(e)}")
        raise

def get_bronze_files_from_s3(prefix=''):
    """List Bronze files from S3"""
    try:
        s3_client = boto3.client('s3', region_name=AWS_REGION)
        response = s3_client.list_objects_v2(
            Bucket=AWS_S3_BRONZE_BUCKET,
            Prefix=prefix
        )

        if 'Contents' not in response:
            logger.warning(f"No files found in S3: s3://{AWS_S3_BRONZE_BUCKET}/{prefix}")
            return []

        files = [obj['Key'] for obj in response['Contents']]
        logger.info(f"Found {len(files)} files in Bronze S3: {prefix}")
        return files
    except Exception as e:
        logger.error(f"Failed to list S3 files: {str(e)}")
        return []

# ============================================================================
# Firehose Helper Functions (Bronze -> S3 자동 적재)
# ============================================================================

def get_firehose_client():
    """Initialize Firehose client with current AWS credentials"""
    return boto3.client('firehose', region_name=AWS_REGION)

def push_file_to_firehose(firehose_client, file_path, category):
    """Push a single Bronze JSONL file's records to Firehose in batches of 500"""
    records = []
    sent = 0

    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            row['category'] = category  # Firehose 동적 파티셔닝 키
            records.append({'Data': (json.dumps(row, ensure_ascii=False) + '\n').encode('utf-8')})

            if len(records) == 500:
                firehose_client.put_record_batch(DeliveryStreamName=FIREHOSE_STREAM_NAME, Records=records)
                sent += len(records)
                records = []

    if records:
        firehose_client.put_record_batch(DeliveryStreamName=FIREHOSE_STREAM_NAME, Records=records)
        sent += len(records)

    return sent

# ============================================================================
# Data Transformation Functions (from transforms/silver_transform.py)
# ============================================================================

def parse_iso8601_duration(duration_str):
    """Parse ISO 8601 duration format (PT8M46S -> 526 seconds)"""
    if not duration_str or not isinstance(duration_str, str):
        return None

    try:
        import re
        pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
        match = re.match(pattern, duration_str)
        if not match:
            return None

        hours, minutes, seconds = match.groups()
        total_seconds = (
            int(hours or 0) * 3600 +
            int(minutes or 0) * 60 +
            int(seconds or 0)
        )
        return total_seconds if total_seconds > 0 else None
    except Exception as e:
        logger.warning(f"Error parsing duration '{duration_str}': {e}")
        return None

def convert_to_kst(utc_datetime_str):
    """Convert UTC datetime to KST (UTC+9)"""
    from datetime import datetime, timedelta, timezone

    if not utc_datetime_str:
        return None

    try:
        if isinstance(utc_datetime_str, str):
            dt = datetime.fromisoformat(utc_datetime_str.replace('Z', '+00:00'))
        else:
            dt = utc_datetime_str

        kst = timezone(timedelta(hours=9))
        kst_dt = dt.astimezone(kst)
        return kst_dt.isoformat()
    except Exception as e:
        logger.warning(f"Error converting to KST: {e}")
        return utc_datetime_str

def transform_to_silver(record):
    """Transform individual record from Bronze to Silver format"""
    try:
        silver_record = {
            'record_id': record.get('_id'),
            'domain': record.get('domain'),
            'event_type': record.get('event_type'),
            'occurred_at_kst': convert_to_kst(record.get('occurred_at')),
            'generated_at_utc': record.get('generated_at_utc'),
            'trace_id': record.get('trace_id'),
            'run_id': record.get('run_id'),

            # Client Info
            'client_country': record.get('client', {}).get('country'),
            'client_platform': record.get('client', {}).get('platform'),
            'client_device_type': record.get('client', {}).get('device_type'),

            # Request Info
            'request_method': record.get('request', {}).get('method'),
            'request_path': record.get('request', {}).get('path'),
            'request_bytes': record.get('request', {}).get('request_bytes'),

            # Response Info
            'response_status_code': record.get('response', {}).get('status_code'),
            'response_latency_ms': record.get('response', {}).get('latency_ms'),
            'response_bytes': record.get('response', {}).get('response_bytes'),

            # Video Specific Data
            'video_id': record.get('data', {}).get('video_id'),
            'video_title': record.get('data', {}).get('title'),
            'video_duration_seconds': parse_iso8601_duration(
                record.get('data', {}).get('duration')
            ),
            'channel_name': record.get('data', {}).get('channel_name'),
            'view_count': record.get('data', {}).get('view_count'),
            'like_count': record.get('data', {}).get('like_count'),
            'comment_count': record.get('data', {}).get('comment_count'),

            # Quality Metrics
            'is_valid': validate_record(record),
            'transformation_timestamp': datetime.utcnow().isoformat(),
        }

        return silver_record
    except Exception as e:
        logger.error(f"Error transforming record: {e}")
        return None

def validate_record(record):
    """Validate critical fields in Bronze record"""
    required_fields = ['_id', 'domain', 'event_type']
    return all(field in record and record[field] is not None for field in required_fields)

def transform_jsonl_file(file_path):
    """Transform JSONL file from Bronze to Silver format"""
    silver_records = []
    error_count = 0

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    record = json.loads(line.strip())
                    silver_record = transform_to_silver(record)
                    if silver_record:
                        silver_records.append(silver_record)
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON at line {line_num}: {e}")
                    error_count += 1

        logger.info(f"✓ Transformed {len(silver_records)} records from {file_path}")
        if error_count > 0:
            logger.warning(f"⚠ {error_count} records failed to parse")

        return silver_records
    except Exception as e:
        logger.error(f"✗ Error transforming file {file_path}: {e}")
        raise

# ============================================================================
# DAG Tasks
# ============================================================================

def push_bronze_to_firehose(**context):
    """TASK 0: Bronze 원본 데이터를 Firehose로 전송 -> S3 자동 파티셔닝 적재 (병렬 실행)"""
    logger.info("=" * 80)
    logger.info("TASK 0: Push Bronze -> Firehose (S3 자동 적재)")
    logger.info("=" * 80)

    bronze_path = Path(BRONZE_DIR)
    jsonl_files = sorted(bronze_path.glob('*.jsonl'))

    if not jsonl_files:
        logger.warning(f"No JSONL files found in {BRONZE_DIR}. Skipping Firehose push.")
        context['task_instance'].xcom_push(key='firehose_push_stats', value={'files_pushed': 0, 'records_sent': 0, 'files': []})
        return

    try:
        firehose_client = get_firehose_client()
    except Exception as e:
        logger.error(f"Firehose client init failed: {e}")
        logger.warning("Continuing without Firehose push (local mode)")
        context['task_instance'].xcom_push(key='firehose_push_stats', value={'files_pushed': 0, 'records_sent': 0, 'files': [], 'error': str(e)})
        return

    push_stats = {'files_pushed': 0, 'records_sent': 0, 'files': []}

    for jsonl_file in jsonl_files:
        category = None
        for key in CATEGORY_MAP:
            if key in jsonl_file.name:
                category = CATEGORY_MAP[key]
                break

        if category is None:
            logger.warning(f"Unable to determine category for {jsonl_file.name}, skipping")
            continue

        try:
            sent = push_file_to_firehose(firehose_client, jsonl_file, category)
            push_stats['files_pushed'] += 1
            push_stats['records_sent'] += sent
            push_stats['files'].append(jsonl_file.name)
            logger.info(f"{jsonl_file.name} -> Firehose 전송 완료 ({sent}건, category={category})")
        except Exception as e:
            logger.error(f"Failed to push {jsonl_file.name} to Firehose: {e}")
            # Firehose 실패해도 파이프라인은 계속 진행 (Silver 변환은 로컬 파일 기준으로 별도 진행)

    context['task_instance'].xcom_push(key='firehose_push_stats', value=push_stats)

    logger.info(f"Firehose push complete: {push_stats['files_pushed']} files, {push_stats['records_sent']} records")
    logger.info("=" * 80)

def validate_bronze(**context):
    """Validate Bronze data directory and prepare file list"""
    logger.info("=" * 80)
    logger.info("TASK 1: Validate Bronze Data")
    logger.info("=" * 80)

    bronze_path = Path(BRONZE_DIR)

    # Check if Bronze directory exists
    if not bronze_path.exists():
        raise AirflowException(f"Bronze directory not found: {BRONZE_DIR}")

    # Find all JSONL files
    jsonl_files = sorted(bronze_path.glob('*.jsonl'))

    if not jsonl_files:
        raise AirflowException(f"No JSONL files found in {BRONZE_DIR}")

    logger.info(f"✓ Found {len(jsonl_files)} Bronze files:")
    for file in jsonl_files:
        file_size = file.stat().st_size / 1024  # KB
        logger.info(f"  - {file.name} ({file_size:.2f} KB)")

    # Store file list in XCom for next task
    file_list = [str(f) for f in jsonl_files]
    context['task_instance'].xcom_push(key='bronze_files', value=file_list)

    logger.info(f"\n✓ Bronze validation successful")
    logger.info("=" * 80)

def transform_to_silver_task(**context):
    """Execute transformation from Bronze to Silver layer"""
    logger.info("=" * 80)
    logger.info("TASK 2: Transform Bronze → Silver")
    logger.info("=" * 80)

    # Retrieve file list from previous task
    task_instance = context['task_instance']
    bronze_files = task_instance.xcom_pull(task_ids='validate_bronze', key='bronze_files')

    if not bronze_files:
        raise AirflowException("No Bronze files found from validation task")

    # Create Silver directory
    silver_path = Path(SILVER_DIR)
    silver_path.mkdir(parents=True, exist_ok=True)

    all_silver_records = []
    transformation_stats = {
        'files_processed': 0,
        'total_records': 0,
        'silver_files_created': []
    }

    # Process each Bronze file
    for bronze_file in bronze_files:
        logger.info(f"\nProcessing: {bronze_file}")

        try:
            # Transform records
            silver_records = transform_jsonl_file(bronze_file)
            transformation_stats['files_processed'] += 1
            transformation_stats['total_records'] += len(silver_records)

            # Write to Silver JSONL file
            file_name = Path(bronze_file).stem
            silver_file = silver_path / f"silver_{file_name}.jsonl"

            with open(silver_file, 'w', encoding='utf-8') as f:
                for record in silver_records:
                    f.write(json.dumps(record, ensure_ascii=False) + '\n')

            transformation_stats['silver_files_created'].append(str(silver_file))
            logger.info(f"✓ Saved to: {silver_file}")

            all_silver_records.extend(silver_records)
        except Exception as e:
            logger.error(f"✗ Failed to process {bronze_file}: {e}")
            raise

    # Push statistics to XCom
    task_instance.xcom_push(key='transformation_stats', value=transformation_stats)

    logger.info(f"\n✓ Transformation complete:")
    logger.info(f"  Files processed: {transformation_stats['files_processed']}")
    logger.info(f"  Total records: {transformation_stats['total_records']}")
    logger.info(f"  Silver files created: {len(transformation_stats['silver_files_created'])}")
    logger.info("=" * 80)

def upload_to_s3_task(**context):
    """Upload Silver data to AWS S3"""
    logger.info("=" * 80)
    logger.info("TASK 3: Upload to AWS S3")
    logger.info("=" * 80)

    if not AWS_S3_SILVER_BUCKET:
        logger.warning("AWS_S3_SILVER_BUCKET not configured. Skipping S3 upload.")
        return

    task_instance = context['task_instance']
    silver_files = task_instance.xcom_pull(
        task_ids='transform_to_silver',
        key='transformation_stats'
    )['silver_files_created']

    if not silver_files:
        logger.warning("No Silver files to upload")
        return

    try:
        s3_client = get_s3_client()
        year = datetime.utcnow().year
        month = datetime.utcnow().month
        day = datetime.utcnow().day

        uploaded_files = []

        for silver_file in silver_files:
            file_name = Path(silver_file).name
            s3_key = f'youtube/silver/year={year}/month={month:02d}/day={day:02d}/{file_name}'

            logger.info(f"Uploading: {file_name}")
            upload_to_s3(silver_file, s3_key, AWS_S3_SILVER_BUCKET)
            uploaded_files.append(s3_key)

        # Store uploaded paths
        task_instance.xcom_push(key='uploaded_s3_paths', value=uploaded_files)

        logger.info(f"\n✓ Successfully uploaded {len(uploaded_files)} files to S3")
        logger.info("=" * 80)
    except Exception as e:
        logger.error(f"✗ S3 upload failed: {e}")
        # Don't fail the DAG if S3 is not available (for local dev)
        logger.warning("Continuing without S3 upload (local mode)")

def generate_report(**context):
    """Generate transformation report"""
    logger.info("=" * 80)
    logger.info("TASK 4: Generate Report")
    logger.info("=" * 80)

    task_instance = context['task_instance']
    stats = task_instance.xcom_pull(
        task_ids='transform_to_silver',
        key='transformation_stats'
    )

    uploaded_files = task_instance.xcom_pull(
        task_ids='upload_to_s3',
        key='uploaded_s3_paths'
    ) or []

    report = {
        'execution_date': context['execution_date'].isoformat(),
        'dag_run_id': context['dag_run'].run_id,
        'transformation': stats,
        's3_uploads': {
            'bucket': AWS_S3_SILVER_BUCKET,
            'files_uploaded': len(uploaded_files),
            'files': uploaded_files
        },
        'status': 'success'
    }

    # Save report
    reports_path = Path(REPORTS_DIR)
    reports_path.mkdir(parents=True, exist_ok=True)

    report_file = reports_path / f"transformation_report_{context['execution_date'].strftime('%Y%m%d_%H%M%S')}.json"

    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info(f"✓ Report saved to: {report_file}")
    logger.info(json.dumps(report, indent=2, ensure_ascii=False))
    logger.info("=" * 80)

def summary_task(**context):
    """Final summary of pipeline execution"""
    logger.info("=" * 80)
    logger.info("PIPELINE EXECUTION SUMMARY")
    logger.info("=" * 80)

    task_instance = context['task_instance']
    stats = task_instance.xcom_pull(
        task_ids='transform_to_silver',
        key='transformation_stats'
    )

    logger.info(f"\n📊 Data Pipeline Results:")
    logger.info(f"  ✓ Files processed: {stats['files_processed']}")
    logger.info(f"  ✓ Records transformed: {stats['total_records']}")
    logger.info(f"  ✓ Silver files created: {len(stats['silver_files_created'])}")

    if AWS_S3_SILVER_BUCKET:
        uploaded = task_instance.xcom_pull(
            task_ids='upload_to_s3',
            key='uploaded_s3_paths'
        ) or []
        logger.info(f"  ✓ Files uploaded to S3: {len(uploaded)}")
        logger.info(f"  ✓ S3 Bucket: {AWS_S3_SILVER_BUCKET}")

    firehose_stats = task_instance.xcom_pull(
        task_ids='push_bronze_to_firehose',
        key='firehose_push_stats'
    )
    if firehose_stats:
        logger.info(f"  ✓ Bronze files pushed to Firehose: {firehose_stats.get('files_pushed', 0)}")
        logger.info(f"  ✓ Records sent to Firehose: {firehose_stats.get('records_sent', 0)}")

    logger.info(f"\n🎉 Pipeline completed successfully!")
    logger.info("=" * 80)

# ============================================================================
# DAG Definition
# ============================================================================

dag = DAG(
    dag_id=DAG_ID,
    default_args=DEFAULT_ARGS,
    schedule_interval=SCHEDULE_INTERVAL,
    description='Bronze to Silver ETL with AWS S3 Integration',
    tags=['etl', 'silver', 'aws', 's3'],
    catchup=False,
    doc_md=__doc__,
)

with dag:
    # Task 1: Validate Bronze
    validate_bronze_task = PythonOperator(
        task_id='validate_bronze',
        python_callable=validate_bronze,
        provide_context=True,
    )

    # Task 2: Transform to Silver
    transform_silver_task = PythonOperator(
        task_id='transform_to_silver',
        python_callable=transform_to_silver_task,
        provide_context=True,
    )

    # Task 3: Upload to S3 (optional, fails gracefully if not configured)
    upload_s3_task = PythonOperator(
        task_id='upload_to_s3',
        python_callable=upload_to_s3_task,
        provide_context=True,
    )

    # Task 4: Generate Report
    report_task = PythonOperator(
        task_id='generate_report',
        python_callable=generate_report,
        provide_context=True,
    )

    # Task 5: Summary
    summary_task_op = PythonOperator(
        task_id='summary',
        python_callable=summary_task,
        provide_context=True,
    )

    # Task 0: Push Bronze to Firehose (S3 자동 적재, 메인 파이프라인과 병렬 실행)
    push_firehose_task = PythonOperator(
        task_id='push_bronze_to_firehose',
        python_callable=push_bronze_to_firehose,
        provide_context=True,
    )

    wait_for_firehose_buffer = BashOperator(
        task_id='wait_for_firehose_buffer',
        bash_command='sleep 90',  # Firehose 버퍼링 시간(60초) + 여유
    )

    # Define dependencies
    validate_bronze_task >> transform_silver_task >> [upload_s3_task, report_task] >> summary_task_op
    push_firehose_task >> wait_for_firehose_buffer >> summary_task_op

if __name__ == "__main__":
    dag.cli()
