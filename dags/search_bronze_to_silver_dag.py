"""
Search 기반 일일 수집 Lambda(daily_search_collector, lambda/youtube_api_daily.py) Bronze -> Silver DAG

Lambda가 S3 Bronze 버킷에 직접 저장한 평면(join된) JSON Lines
(s3://bronze/bronze/search/category=<slug>/year=Y/month=M/day=D/<slug>_<run_date>_<HHMMSS>.jsonl)
을 읽어서, 배치 파이프라인(bronze_to_silver_dag_aws.py)과 동일한 변환/오염 판정 기준으로
Silver S3에 저장한다. 로컬 디스크를 거치지 않고 S3 -> S3로 바로 처리한다.

Bronze 레코드가 이미 search()+videos.list+channels.list를 join한 평면 구조라
(daily_mostpopular_collector.py의 중첩 videos_by_category/channels 구조와 다름),
변환 함수는 bronze_to_silver_dag_aws.py의 transform_to_silver()/validate_record()와
필드 매핑이 동일 - 그 로직을 그대로 재사용한다(trending_rank 개념 없음: search.list는
mostPopular 같은 순위 데이터가 아니라 order=date 결과라 순위가 의미 없음).

2026-09-03: 이 DAG와 완전히 동일한 로직(같은 Silver 경로, 같은 파일명 규칙, 멱등적
쓰기)을 AWS Step Functions 상태머신(infra/stepfunctions.tf의 search_to_silver,
lambda/stepfn_list_bronze_search.py + lambda/stepfn_transform_search_silver.py)으로
옮겼고, 이후 infra/pipeline_orchestrator.tf의 마스터 상태머신(pipeline_orchestrator)이
Bronze(daily_search_collector) 완료를 기다렸다가 이 Step Functions 경로를
states:startExecution.sync:2로 중첩 실행하는 방식으로 자동화됐다("브론즈 끝나고
실버 전환, 실버 끝나고 골드 전환 - 앞 단계가 안 끝나면 다음 단계 시작 금지" 요구사항
때문에, 독립적으로 시간만 보고 도는 이 Airflow DAG의 매시 40분 자동 스케줄로는 그
순서를 보장할 수 없었음).

dags/silver_to_gold_dag.py(Athena 기반 gold_compute_athena로 대체된 것)와 같은 이유로,
이 DAG도 삭제하지 않고 자동 스케줄만 영구히 끈다(schedule_interval=None,
is_paused_upon_creation=True) - 코드는 그대로 보존해서 필요할 때
`airflow dags trigger search_bronze_to_silver`로 수동 롤백 경로로만 쓸 수 있게 남겨둔다.
"""
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import re

import pendulum

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago
from airflow.exceptions import AirflowException

import boto3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AWS_REGION = os.getenv('AWS_DEFAULT_REGION', 'us-west-2')
AWS_S3_BRONZE_BUCKET = os.getenv('AWS_S3_BRONZE_BUCKET')
AWS_S3_SILVER_BUCKET = os.getenv('AWS_S3_SILVER_BUCKET')

DAG_ID = 'search_bronze_to_silver'
LOCAL_TZ = pendulum.timezone('Asia/Seoul')
# 데이터 파티션(bronze/search/.../year=/month=/day=) 및 '오늘' 계산 기준: KST(한국시간)
KST = timezone(timedelta(hours=9))
DEFAULT_ARGS = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': pendulum.datetime(2024, 1, 1, tz=LOCAL_TZ),
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}
# 예전: Lambda EventBridge 스케줄(매시간 30분)보다 10분 늦게 실행해서 새로 생긴 데이터를 처리
# 주의: Airflow schedule_interval은 표준 5필드 cron(croniter)이라 AWS cron의 '?'/6필드 문법은 못 씀
# SCHEDULE_INTERVAL = '40 * * * *'
# 2026-09-03: infra/pipeline_orchestrator.tf가 Bronze 완료 후 이 DAG와 동일한 로직인
# Step Functions(search_to_silver)를 직접 중첩 실행하며 대체함에 따라, 이 Airflow DAG의
# 자동 스케줄은 영구히 끔(None = 수동 트리거만 가능). dags/silver_to_gold_dag.py와
# 동일한 방식의 수동 롤백 경로로 유지 - 위 docstring 참고.
SCHEDULE_INTERVAL = None

# lambda/youtube_api_daily.py의 CATEGORY_SLUGS와 동일
CATEGORY_SLUGS = ['film_animation', 'autos_vehicles', 'gaming', 'people_blogs']

# ---- 배치 파이프라인(bronze_to_silver_dag_aws.py)과 동일한 카테고리/오염 판정 기준 ----
CATEGORY_ID_MAP = {
    '1':  'film_animation',
    '2':  'autos_vehicles',
    '20': 'gaming',
    '22': 'people_blogs',
}
# 22(인물·블로그)는 lambda/youtube_api_daily.py에서도 "실제 브이로그 추적이 아니라 다른
# 카테고리로 오분류된 리뷰어를 잡아내는 용도"로만 최소 수집함 - 정상 분석 대상이 아니므로
# 배치 파이프라인과 동일하게 오염 데이터로 분리
CONTAMINATED_CATEGORY_IDS = {'22'}


def get_s3_client():
    return boto3.client('s3', region_name=AWS_REGION)


def safe_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def parse_iso8601_duration(duration_str):
    if not duration_str or not isinstance(duration_str, str):
        return None
    pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
    match = re.match(pattern, duration_str)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    total_seconds = int(hours or 0) * 3600 + int(minutes or 0) * 60 + int(seconds or 0)
    return total_seconds if total_seconds > 0 else None


def get_video_type(duration_seconds):
    if duration_seconds is None:
        return 'unknown'
    if duration_seconds < 240:
        return 'short'
    elif duration_seconds <= 1200:
        return 'medium'
    else:
        return 'long'


def convert_to_kst(utc_datetime_str):
    if not utc_datetime_str:
        return None
    try:
        dt = datetime.fromisoformat(utc_datetime_str.replace('Z', '+00:00'))
        kst = timezone(timedelta(hours=9))
        return dt.astimezone(kst).isoformat()
    except Exception as e:
        logger.warning(f"Error converting to KST: {e}")
        return utc_datetime_str


def validate_record(record):
    """배치 파이프라인의 validate_record()와 동일한 기준"""
    required_fields = ['video_id', 'channel_id', 'published_at', 'title']
    if not all(record.get(f) not in (None, '') for f in required_fields):
        return False

    for count_field in ('view_count', 'like_count', 'comment_count'):
        val = safe_int(record.get(count_field))
        if val is not None and val < 0:
            return False

    if str(record.get('category_id')) in CONTAMINATED_CATEGORY_IDS:
        return False

    return True


def transform_to_silver(record):
    """배치 파이프라인의 transform_to_silver()와 동일한 출력 스키마 (source 필드만 추가)"""
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

        return {
            'video_id': record.get('video_id'),
            'title': record.get('title'),
            'description': record.get('description'),
            'channel_id': record.get('channel_id'),
            'channel_name': record.get('channel_name'),

            'category_id': safe_int(category_id) if category_id else None,
            'category_name': record.get('category_name'),
            'category_slug': CATEGORY_ID_MAP.get(category_id, 'unknown'),

            'tags': record.get('tags', []),
            'matched_tags': record.get('matched_tags', []),
            'topic_categories': record.get('topic_categories', []),

            'view_count': view_count,
            'like_count': like_count,
            'comment_count': comment_count,
            'engagement_rate': engagement_rate,

            'duration_iso8601': record.get('duration'),
            'duration_seconds': duration_seconds,
            'video_type': get_video_type(duration_seconds),

            'definition': record.get('definition'),
            'is_hd': record.get('definition') == 'hd',
            'caption_available': str(record.get('caption')).lower() == 'true',
            'has_paid_product_placement': bool(record.get('has_paid_product_placement', False)),
            'privacy_status': record.get('privacy_status'),
            'made_for_kids': record.get('made_for_kids'),
            'live_broadcast_content': record.get('live_broadcast_content'),
            'is_live_content': record.get('live_broadcast_content') not in (None, 'none'),
            'default_audio_language': record.get('default_audio_language') or None,
            'thumbnail_url': record.get('thumbnail_url') or None,

            'published_at_utc': published_at,
            'published_at_kst': convert_to_kst(published_at),
            'published_year_month': published_at[:7] if published_at else None,

            'channel_published_at_utc': record.get('channel_published_at'),
            'subscriber_count': safe_int(record.get('subscriber_count')),
            'hidden_subscriber_count': bool(record.get('hidden_subscriber_count', False)),
            'channel_total_view_count': safe_int(record.get('channel_total_view_count')),
            'channel_total_video_count': safe_int(record.get('channel_total_video_count')),
            'uploads_playlist_id': record.get('uploads_playlist_id') or None,
            'channel_thumbnail_url': record.get('channel_thumbnail_url') or None,

            'collected_at_utc': record.get('collected_at_utc'),

            'is_valid': validate_record(record),
            'silver_transformed_at_utc': datetime.utcnow().isoformat(),
            'source': 'daily_search_collector',
        }
    except Exception as e:
        logger.error(f"Error transforming record: {e}")
        return None


# ============================================================================
# DAG Tasks
# ============================================================================

def list_bronze_search_objects(**context):
    logger.info("=" * 80)
    logger.info("TASK 1: List bronze/search/ objects in Bronze S3 (오늘 날짜 파티션)")
    logger.info("=" * 80)

    if not AWS_S3_BRONZE_BUCKET:
        raise AirflowException("AWS_S3_BRONZE_BUCKET not configured")

    s3 = get_s3_client()
    today = datetime.now(KST)
    year, month, day = today.strftime('%Y'), today.strftime('%m'), today.strftime('%d')

    keys = []
    for slug in CATEGORY_SLUGS:
        prefix = f'bronze/search/category={slug}/year={year}/month={month}/day={day}/'
        response = s3.list_objects_v2(Bucket=AWS_S3_BRONZE_BUCKET, Prefix=prefix)
        keys.extend(obj['Key'] for obj in response.get('Contents', []) if obj['Key'].endswith('.jsonl'))

    logger.info(f"Found {len(keys)} bronze/search objects for {year}-{month}-{day}")
    context['task_instance'].xcom_push(key='bronze_keys', value=keys)


def transform_search_to_silver(**context):
    logger.info("=" * 80)
    logger.info("TASK 2: Transform search Bronze data -> Silver")
    logger.info("=" * 80)

    task_instance = context['task_instance']
    keys = task_instance.xcom_pull(task_ids='list_bronze_search_objects', key='bronze_keys')

    stats = {'files_processed': 0, 'valid_records': 0, 'rejected_records': 0, 'uploaded': []}

    if not keys:
        logger.warning("No new bronze/search objects to process")
        task_instance.xcom_push(key='stats', value=stats)
        return

    if not AWS_S3_SILVER_BUCKET:
        raise AirflowException("AWS_S3_SILVER_BUCKET not configured")

    s3 = get_s3_client()

    for key in keys:
        obj = s3.get_object(Bucket=AWS_S3_BRONZE_BUCKET, Key=key)
        lines = obj['Body'].read().decode('utf-8').splitlines()

        m = re.search(r'category=([^/]+)/year=(\d+)/month=(\d+)/day=(\d+)/', key)
        slug, year, month, day = m.groups() if m else ('unknown', '0000', '00', '00')
        fname_suffix = os.path.basename(key)[:-len('.jsonl')]

        valid_records, rejected_records = [], []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in {key}: {e}")
                continue
            silver = transform_to_silver(record)
            if not silver:
                continue
            if silver['is_valid']:
                valid_records.append(silver)
                stats['valid_records'] += 1
            else:
                rejected_records.append(silver)
                stats['rejected_records'] += 1

        for records, prefix_root, fname_prefix in (
            (valid_records, 'youtube/silver', 'silver'),
            (rejected_records, 'youtube/silver-rejected', 'rejected'),
        ):
            if not records:
                continue
            fname = f"{fname_prefix}_{fname_suffix}.jsonl"
            s3_key = f'{prefix_root}/category={slug}/year={year}/month={month}/day={day}/{fname}'
            body = "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"
            s3.put_object(Bucket=AWS_S3_SILVER_BUCKET, Key=s3_key, Body=body.encode('utf-8'))
            stats['uploaded'].append(s3_key)
            logger.info(f"Uploaded {len(records)} records -> s3://{AWS_S3_SILVER_BUCKET}/{s3_key}")

        stats['files_processed'] += 1

    task_instance.xcom_push(key='stats', value=stats)
    logger.info(f"Done: files={stats['files_processed']} valid={stats['valid_records']} rejected={stats['rejected_records']}")
    logger.info("=" * 80)


dag = DAG(
    dag_id=DAG_ID,
    default_args=DEFAULT_ARGS,
    schedule_interval=SCHEDULE_INTERVAL,
    description='[대체됨 - infra/pipeline_orchestrator.tf의 search_to_silver 중첩 실행 참고, 수동 롤백용으로 유지] '
                'Lambda 일일 수집(daily_search_collector, search.list 기반) 데이터를 Silver로 변환 (S3 -> S3)',
    tags=['etl', 'silver', 'lambda', 'daily', 'search', 'superseded-by-stepfunctions', 'manual-rollback-only'],
    catchup=False,
    is_paused_upon_creation=True,
    doc_md=__doc__,
)

with dag:
    list_task = PythonOperator(
        task_id='list_bronze_search_objects',
        python_callable=list_bronze_search_objects,
        provide_context=True,
    )
    transform_task = PythonOperator(
        task_id='transform_search_to_silver',
        python_callable=transform_search_to_silver,
        provide_context=True,
    )
    list_task >> transform_task

if __name__ == "__main__":
    dag.cli()
