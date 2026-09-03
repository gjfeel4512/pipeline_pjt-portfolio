"""
Bronze to Silver ETL DAG with AWS S3 Integration
Transforms raw YouTube data from Bronze to Silver layer and uploads to S3

Architecture:
- Bronze Layer: Raw data from data collection (local/S3)
- Silver Layer: Cleaned, validated, transformed data
- Storage: Local (for development) + AWS S3 (for production)
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import logging
import os

import pendulum

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
AWS_CLOUDWATCH_LOG_GROUP = os.getenv('AWS_CLOUDWATCH_LOG_GROUP', '/aws/airflow/goldline-dev')

# Firehose Configuration (Bronze 자동 적재)
FIREHOSE_STREAM_NAME = os.getenv('FIREHOSE_STREAM_NAME', 'goldline-dev-bronze-stream')

# YouTube 카테고리 ID -> 영문 슬러그 매핑 (실제 Bronze 데이터의 category_id 필드 기준)
CATEGORY_ID_MAP = {
    '1':  'film_animation',   # 영화_애니메이션
    '2':  'autos_vehicles',   # 자동차_차량
    '20': 'gaming',           # 게임
    '22': 'people_blogs',     # 인물_블로그 (주의: 업로드시 카테고리 미선택시 YouTube 기본값)
}

# Silver 변환 시 오염 데이터로 처리할 category_id 목록
# 22(인물·블로그)는 업로더가 카테고리를 지정하지 않았을 때 YouTube가 자동으로 부여하는
# 기본값이라 실제 콘텐츠 성격을 신뢰할 수 없음 -> Silver에서는 정상 3개 카테고리
# (1=영화·애니, 2=자동차·차량, 20=게임)와 분리하여 오염 데이터로 처리
CONTAMINATED_CATEGORY_IDS = {'22'}

# 이 DAG 자체의 Silver 업로드 파티션(year/month/day) 계산 기준: KST(한국시간)
KST = timezone(timedelta(hours=9))

# DAG Configuration
DAG_ID = 'bronze_to_silver_with_s3'
LOCAL_TZ = pendulum.timezone('Asia/Seoul')
DEFAULT_ARGS = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': pendulum.datetime(2024, 1, 1, tz=LOCAL_TZ),
    'email': ['airflow@pipeline.local'],
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}

# SCHEDULE_INTERVAL = '0 2 * * *'  # Daily at 02:00 KST
SCHEDULE_INTERVAL = '40 * * * *'  # 임시

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

def push_file_to_firehose(firehose_client, file_path):
    """Push a single Bronze JSONL file's records to Firehose in batches of 500
    카테고리는 각 레코드의 category_id 필드에서 직접 추출 (파일명에 의존하지 않음)"""
    records = []
    sent = 0

    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            cat_id = str(row.get('category_id', ''))
            row['category'] = CATEGORY_ID_MAP.get(cat_id, 'unknown')  # Firehose 동적 파티셔닝 키
            # Firehose S3 prefix의 year=/month=/day=는 !{timestamp:...}를 쓰면 항상
            # UTC 기준이라 KST와 어긋난다 - 레코드에 KST 날짜를 직접 실어서
            # !{partitionKeyFromQuery:...}로 대체한다 (infra/firehose.tf 참고)
            try:
                collected_kst = datetime.fromisoformat(
                    row.get('collected_at_utc', '').replace('Z', '+00:00')
                ).astimezone(KST)
            except (ValueError, AttributeError):
                collected_kst = datetime.now(KST)
            row['year_kst'] = collected_kst.strftime('%Y')
            row['month_kst'] = collected_kst.strftime('%m')
            row['day_kst'] = collected_kst.strftime('%d')
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

def safe_int(value):
    """Bronze의 통계 필드(view_count 등)는 문자열로 저장되어 있어 안전하게 정수로 변환"""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None

def get_video_type(duration_seconds):
    """YouTube 영상 길이 분류 (수집 시 사용한 기준과 동일: short<4분, medium 4~20분, long>20분)"""
    if duration_seconds is None:
        return 'unknown'
    if duration_seconds < 240:
        return 'short'
    elif duration_seconds <= 1200:
        return 'medium'
    else:
        return 'long'

def transform_to_silver(record):
    """Transform individual record from Bronze(YouTube API 원본) to Silver format"""
    try:
        duration_seconds = parse_iso8601_duration(record.get('duration'))
        view_count = safe_int(record.get('view_count'))
        like_count = safe_int(record.get('like_count'))
        comment_count = safe_int(record.get('comment_count'))

        engagement_rate = None
        if view_count and view_count > 0:
            engagement_rate = round(((like_count or 0) + (comment_count or 0)) / view_count, 6)

        category_id = str(record.get('category_id', ''))
        published_at = record.get('published_at')

        silver_record = {
            'video_id': record.get('video_id'),
            'title': record.get('title'),
            'description': record.get('description'),
            'channel_id': record.get('channel_id'),
            'channel_name': record.get('channel_name'),

            # 카테고리
            'category_id': safe_int(category_id) if category_id else None,
            'category_name': record.get('category_name'),
            'category_slug': CATEGORY_ID_MAP.get(category_id, 'unknown'),

            'tags': record.get('tags', []),

            # 통계 (문자열 -> 정수 변환)
            'view_count': view_count,
            'like_count': like_count,
            'comment_count': comment_count,
            'engagement_rate': engagement_rate,

            # 영상 길이
            'duration_iso8601': record.get('duration'),
            'duration_seconds': duration_seconds,
            'video_type': get_video_type(duration_seconds),

            # 화질/자막/공개상태
            'definition': record.get('definition'),
            'is_hd': record.get('definition') == 'hd',
            'caption_available': str(record.get('caption')).lower() == 'true',
            'privacy_status': record.get('privacy_status'),
            'live_broadcast_content': record.get('live_broadcast_content'),
            'is_live_content': record.get('live_broadcast_content') not in (None, 'none'),

            # 시간 정보
            'published_at_utc': published_at,
            'published_at_kst': convert_to_kst(published_at),
            'published_year_month': published_at[:7] if published_at else None,

            # 채널 정보
            'channel_published_at_utc': record.get('channel_published_at'),
            'subscriber_count': safe_int(record.get('subscriber_count')),
            'hidden_subscriber_count': bool(record.get('hidden_subscriber_count', False)),
            'channel_total_view_count': safe_int(record.get('channel_total_view_count')),
            'channel_total_video_count': safe_int(record.get('channel_total_video_count')),
            # 이 파이프라인(youtube_api_collector.py 1년치 백필)의 Bronze는 아직
            # uploads_playlist_id/channel_thumbnail_url을 수집하지 않아 현재는 항상
            # None - 다른 두 Silver DAG와 dim_channel 스키마를 맞추기 위해 필드만 유지
            'uploads_playlist_id': record.get('uploads_playlist_id') or None,
            'channel_thumbnail_url': record.get('channel_thumbnail_url') or None,

            'collected_at_utc': record.get('collected_at_utc'),

            # 품질 지표
            'is_valid': validate_record(record),
            'silver_transformed_at_utc': datetime.utcnow().isoformat(),
        }

        return silver_record
    except Exception as e:
        logger.error(f"Error transforming record: {e}")
        return None

def validate_record(record):
    """Validate critical fields in Bronze record (실제 YouTube API 원본 스키마 기준)"""
    required_fields = ['video_id', 'channel_id', 'published_at', 'title']
    if not all(field in record and record[field] not in (None, '') for field in required_fields):
        return False

    # 통계 필드가 음수이거나 파싱 불가능하면 오염 데이터로 간주
    for count_field in ('view_count', 'like_count', 'comment_count'):
        val = safe_int(record.get(count_field))
        if val is not None and val < 0:
            return False

    # category_id=22(인물·블로그)는 카테고리 미선택 시 YouTube 기본값으로 자동 지정되므로
    # 신뢰할 수 없는 카테고리 정보 -> 오염 데이터로 간주하여 분리
    if str(record.get('category_id')) in CONTAMINATED_CATEGORY_IDS:
        return False

    return True

def transform_jsonl_file(file_path):
    """Transform JSONL file from Bronze to Silver format.
    is_valid=False 레코드(category_id=22 '인물/블로그' 포함)는 별도로 분리하여 반환한다."""
    valid_records = []
    rejected_records = []
    error_count = 0

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    record = json.loads(line.strip())
                    silver_record = transform_to_silver(record)
                    if silver_record:
                        if silver_record.get('is_valid'):
                            valid_records.append(silver_record)
                        else:
                            rejected_records.append(silver_record)
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON at line {line_num}: {e}")
                    error_count += 1

        logger.info(f"✓ Transformed {len(valid_records)} valid / {len(rejected_records)} rejected records from {file_path}")
        if error_count > 0:
            logger.warning(f"⚠ {error_count} records failed to parse")

        return valid_records, rejected_records
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
        try:
            sent = push_file_to_firehose(firehose_client, jsonl_file)
            push_stats['files_pushed'] += 1
            push_stats['records_sent'] += sent
            push_stats['files'].append(jsonl_file.name)
            logger.info(f"{jsonl_file.name} -> Firehose 전송 완료 ({sent}건)")
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
    """Execute transformation from Bronze to Silver layer.
    정상 카테고리(1,2,20)는 silver/ 로, 오염 데이터(category_id=22 등)는 silver/rejected/ 로 분리 저장."""
    logger.info("=" * 80)
    logger.info("TASK 2: Transform Bronze → Silver")
    logger.info("=" * 80)

    task_instance = context['task_instance']
    bronze_files = task_instance.xcom_pull(task_ids='validate_bronze', key='bronze_files')

    if not bronze_files:
        raise AirflowException("No Bronze files found from validation task")

    silver_path = Path(SILVER_DIR)
    silver_path.mkdir(parents=True, exist_ok=True)
    rejected_path = silver_path / 'rejected'
    rejected_path.mkdir(parents=True, exist_ok=True)

    transformation_stats = {
        'files_processed': 0,
        'total_records': 0,
        'valid_records': 0,
        'rejected_records': 0,
        'silver_files_created': [],
        'rejected_files_created': []
    }

    for bronze_file in bronze_files:
        logger.info(f"\nProcessing: {bronze_file}")

        try:
            valid_records, rejected_records = transform_jsonl_file(bronze_file)
            transformation_stats['files_processed'] += 1
            transformation_stats['total_records'] += len(valid_records) + len(rejected_records)
            transformation_stats['valid_records'] += len(valid_records)
            transformation_stats['rejected_records'] += len(rejected_records)

            file_name = Path(bronze_file).stem

            if valid_records:
                silver_file = silver_path / f"silver_{file_name}.jsonl"
                with open(silver_file, 'w', encoding='utf-8') as f:
                    for record in valid_records:
                        f.write(json.dumps(record, ensure_ascii=False) + '\n')
                transformation_stats['silver_files_created'].append(str(silver_file))
                logger.info(f"✓ Saved {len(valid_records)} valid records to: {silver_file}")

            if rejected_records:
                rejected_file = rejected_path / f"rejected_{file_name}.jsonl"
                with open(rejected_file, 'w', encoding='utf-8') as f:
                    for record in rejected_records:
                        f.write(json.dumps(record, ensure_ascii=False) + '\n')
                transformation_stats['rejected_files_created'].append(str(rejected_file))
                logger.info(f"⚠ Saved {len(rejected_records)} rejected records to: {rejected_file}")
        except Exception as e:
            logger.error(f"✗ Failed to process {bronze_file}: {e}")
            raise

    task_instance.xcom_push(key='transformation_stats', value=transformation_stats)

    logger.info(f"\n✓ Transformation complete:")
    logger.info(f"  Files processed: {transformation_stats['files_processed']}")
    logger.info(f"  Valid records (1=영화·애니, 2=자동차·차량, 20=게임): {transformation_stats['valid_records']}")
    logger.info(f"  Rejected records (22=인물·블로그 등 오염 데이터): {transformation_stats['rejected_records']}")
    logger.info(f"  Silver files created: {len(transformation_stats['silver_files_created'])}")
    logger.info(f"  Rejected files created: {len(transformation_stats['rejected_files_created'])}")
    logger.info("=" * 80)

def upload_to_s3_task(**context):
    """Upload Silver data (정상) + rejected(오염) data to AWS S3, 각각 다른 prefix로 저장"""
    logger.info("=" * 80)
    logger.info("TASK 3: Upload to AWS S3")
    logger.info("=" * 80)

    if not AWS_S3_SILVER_BUCKET:
        logger.warning("AWS_S3_SILVER_BUCKET not configured. Skipping S3 upload.")
        return

    task_instance = context['task_instance']
    stats = task_instance.xcom_pull(
        task_ids='transform_to_silver',
        key='transformation_stats'
    )
    silver_files = stats.get('silver_files_created', [])
    rejected_files = stats.get('rejected_files_created', [])

    if not silver_files and not rejected_files:
        logger.warning("No Silver/rejected files to upload")
        return

    try:
        s3_client = get_s3_client()
        now_kst = datetime.now(KST)
        year = now_kst.year
        month = now_kst.month
        day = now_kst.day

        uploaded_files = []

        def upload_group(files, prefix_root, filename_strip):
            for file_path in files:
                file_name = Path(file_path).name
                stem = Path(file_path).stem
                name_no_prefix = stem[len(filename_strip):] if stem.startswith(filename_strip) else stem
                category_slug = name_no_prefix.rsplit('_', 1)[0] if '_' in name_no_prefix else 'unknown'
                s3_key = f'{prefix_root}/category={category_slug}/year={year}/month={month:02d}/day={day:02d}/{file_name}'
                logger.info(f"Uploading: {file_name} -> s3://{AWS_S3_SILVER_BUCKET}/{s3_key}")
                upload_to_s3(file_path, s3_key, AWS_S3_SILVER_BUCKET)
                uploaded_files.append(s3_key)

        upload_group(silver_files, 'youtube/silver', 'silver_')
        upload_group(rejected_files, 'youtube/silver-rejected', 'rejected_')

        task_instance.xcom_push(key='uploaded_s3_paths', value=uploaded_files)

        logger.info(f"\n✓ Successfully uploaded {len(uploaded_files)} files to S3")
        logger.info("=" * 80)
    except Exception as e:
        logger.error(f"✗ S3 upload failed: {e}")
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
    logger.info(f"  ✓ Valid records (1=영화·애니, 2=자동차·차량, 20=게임): {stats.get('valid_records', 0)}")
    logger.info(f"  ⚠ Rejected records (22=인물·블로그 등 오염 데이터): {stats.get('rejected_records', 0)}")
    logger.info(f"  ✓ Silver files created: {len(stats['silver_files_created'])}")
    logger.info(f"  ⚠ Rejected files created: {len(stats.get('rejected_files_created', []))}")

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
