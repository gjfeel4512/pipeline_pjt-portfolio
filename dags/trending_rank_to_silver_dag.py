"""
트렌딩 순위 추적(trending_rank_tracker) Bronze -> Silver ETL DAG

Lambda(trending_rank_tracker)가 S3 Bronze 버킷에 직접 저장한 원본 YouTube API 응답
(s3://bronze/trending/dt=YYYY-MM-DD/hh=HH/data.json, videos_by_category+channels 중첩
구조, chart=mostPopular 응답 순서 = trending_rank)을 읽어서 배치 파이프라인
(bronze_to_silver_dag_aws.py)과 동일한 평면 스키마/오염 판정 기준으로 변환한 뒤 Silver
S3에 저장한다. 로컬 디스크를 거치지 않고 S3 -> S3로 바로 처리한다.

이 DAG가 채우는 fact_video_snapshot.trending_rank는 sql/compute_gold.sql의
gold_video_rank_trend(순위 상승/하락, 조회수 증가율)의 유일한 데이터 소스다 -
search_bronze_to_silver_dag.py(youtube_api_daily.py 기반)가 만드는 레코드는
trending_rank가 항상 NULL이라 그 집계에서 자동으로 제외된다.
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

DAG_ID = 'trending_rank_bronze_to_silver'
LOCAL_TZ = pendulum.timezone('Asia/Seoul')
DEFAULT_ARGS = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': pendulum.datetime(2024, 1, 1, tz=LOCAL_TZ),
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
}
# Lambda EventBridge 스케줄(매시간 15분)보다 10분 늦게 실행해서 새로 생긴 데이터를 처리
# 주의: Airflow schedule_interval은 표준 5필드 cron(croniter)이라 AWS cron의 '?'/6필드 문법은 못 씀
SCHEDULE_INTERVAL = '25 * * * *'

# ---- 배치 파이프라인(bronze_to_silver_dag_aws.py)과 동일한 카테고리/오염 판정 기준 ----
CATEGORY_ID_MAP = {
    '1':  'film_animation',
    '2':  'autos_vehicles',
    '20': 'gaming',
    '22': 'people_blogs',
}
CATEGORY_NAME_MAP = {
    '1':  '영화_애니메이션',
    '2':  '자동차_차량',
    '20': '게임',
    '22': '인물_블로그',
}
# 22(인물·블로그)는 카테고리 미선택 시 YouTube 기본값 -> 오염 데이터로 분리 (배치 파이프라인과 동일 기준)
CONTAMINATED_CATEGORY_IDS = {'22'}

# 데이터 파티션(daily/dt=/hh=) 및 '오늘' 계산 기준: KST(한국시간)
KST = timezone(timedelta(hours=9))


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


def validate_flat_record(record):
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


def transform_flat_to_silver(record):
    """배치 파이프라인의 transform_to_silver()와 동일한 출력 스키마 (trending_rank/source 필드만 추가)"""
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
            'trending_rank': record.get('trending_rank'),

            'tags': record.get('tags', []),
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

            'collected_at_utc': record.get('collected_at_utc'),

            'is_valid': validate_flat_record(record),
            'silver_transformed_at_utc': datetime.utcnow().isoformat(),
            'source': 'trending_rank_tracker',
        }
    except Exception as e:
        logger.error(f"Error transforming record: {e}")
        return None


def flatten_trending_payload(payload):
    """Lambda가 저장한 중첩 구조(videos_by_category + channels)를
    배치 파이프라인과 동일한 평면 레코드 리스트로 변환. 응답 순서(enumerate)가 곧 trending_rank."""
    channels_by_id = {c.get('id'): c for c in payload.get('channels', [])}
    collected_at_utc = payload.get('collected_at_utc')
    flat_records = []

    for category_id, videos in payload.get('videos_by_category', {}).items():
        category_name = CATEGORY_NAME_MAP.get(str(category_id), 'unknown')
        for rank, v in enumerate(videos, start=1):
            snippet = v.get('snippet', {})
            content_details = v.get('contentDetails', {})
            statistics = v.get('statistics', {})
            status = v.get('status', {})
            topic_details = v.get('topicDetails', {})
            thumbnails = snippet.get('thumbnails', {})
            thumbnail_url = (
                thumbnails.get('high', {}).get('url')
                or thumbnails.get('medium', {}).get('url')
                or thumbnails.get('default', {}).get('url')
            )
            channel_id = snippet.get('channelId')
            channel = channels_by_id.get(channel_id, {})
            ch_snippet = channel.get('snippet', {})
            ch_statistics = channel.get('statistics', {})
            ch_content_details = channel.get('contentDetails', {})

            flat_records.append({
                'category_name': category_name,
                'category_id': category_id,
                'trending_rank': rank,
                'video_id': v.get('id'),
                'title': snippet.get('title'),
                'description': snippet.get('description'),
                'published_at': snippet.get('publishedAt'),
                'tags': snippet.get('tags', []),
                'topic_categories': topic_details.get('topicCategories', []),
                'live_broadcast_content': snippet.get('liveBroadcastContent'),
                'default_audio_language': snippet.get('defaultAudioLanguage'),
                'thumbnail_url': thumbnail_url,
                'view_count': statistics.get('viewCount'),
                'like_count': statistics.get('likeCount'),
                'comment_count': statistics.get('commentCount'),
                'duration': content_details.get('duration'),
                'definition': content_details.get('definition'),
                'caption': content_details.get('caption'),
                'has_paid_product_placement': content_details.get('hasPaidProductPlacement', False),
                'privacy_status': status.get('privacyStatus'),
                'made_for_kids': status.get('madeForKids'),
                'channel_id': channel_id,
                'channel_name': ch_snippet.get('title'),
                'channel_published_at': ch_snippet.get('publishedAt'),
                'subscriber_count': ch_statistics.get('subscriberCount'),
                'hidden_subscriber_count': ch_statistics.get('hiddenSubscriberCount', False),
                'channel_total_view_count': ch_statistics.get('viewCount'),
                'channel_total_video_count': ch_statistics.get('videoCount'),
                'uploads_playlist_id': ch_content_details.get('relatedPlaylists', {}).get('uploads'),
                'collected_at_utc': collected_at_utc,
            })
    return flat_records


# ============================================================================
# DAG Tasks
# ============================================================================

def list_trending_objects(**context):
    logger.info("=" * 80)
    logger.info("TASK 1: List trending/ objects in Bronze S3")
    logger.info("=" * 80)

    if not AWS_S3_BRONZE_BUCKET:
        raise AirflowException("AWS_S3_BRONZE_BUCKET not configured")

    s3 = get_s3_client()
    today = datetime.now(KST).strftime('%Y-%m-%d')
    prefix = f'trending/dt={today}/'
    response = s3.list_objects_v2(Bucket=AWS_S3_BRONZE_BUCKET, Prefix=prefix)
    keys = [obj['Key'] for obj in response.get('Contents', []) if obj['Key'].endswith('data.json')]

    logger.info(f"Found {len(keys)} trending objects under {prefix}")
    context['task_instance'].xcom_push(key='trending_keys', value=keys)


def transform_trending_to_silver(**context):
    logger.info("=" * 80)
    logger.info("TASK 2: Transform trending Lambda data -> Silver")
    logger.info("=" * 80)

    task_instance = context['task_instance']
    keys = task_instance.xcom_pull(task_ids='list_trending_objects', key='trending_keys')

    stats = {'files_processed': 0, 'valid_records': 0, 'rejected_records': 0, 'uploaded': []}

    if not keys:
        logger.warning("No new trending objects to process")
        task_instance.xcom_push(key='stats', value=stats)
        return

    if not AWS_S3_SILVER_BUCKET:
        raise AirflowException("AWS_S3_SILVER_BUCKET not configured")

    s3 = get_s3_client()

    for key in keys:
        obj = s3.get_object(Bucket=AWS_S3_BRONZE_BUCKET, Key=key)
        payload = json.loads(obj['Body'].read().decode('utf-8'))
        flat_records = flatten_trending_payload(payload)

        m = re.search(r'dt=([\d-]+)/hh=(\d+)', key)
        dt_str, hh_str = (m.group(1), m.group(2)) if m else (datetime.now(KST).strftime('%Y-%m-%d'), '00')
        year, month, day = dt_str.split('-')

        grouped = {}
        for rec in flat_records:
            silver = transform_flat_to_silver(rec)
            if not silver:
                continue
            slug = silver['category_slug']
            bucket_key = 'valid' if silver['is_valid'] else 'rejected'
            grouped.setdefault((slug, bucket_key), []).append(silver)
            if silver['is_valid']:
                stats['valid_records'] += 1
            else:
                stats['rejected_records'] += 1

        for (slug, bucket_key), records in grouped.items():
            prefix_root = 'youtube/silver' if bucket_key == 'valid' else 'youtube/silver-rejected'
            fname_prefix = 'silver' if bucket_key == 'valid' else 'rejected'
            fname = f"{fname_prefix}_trending_{slug}_{dt_str}_{hh_str}.jsonl"
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
    description='Lambda 트렌딩 순위 추적(trending_rank_tracker) 데이터를 Silver로 변환 (S3 -> S3)',
    tags=['etl', 'silver', 'lambda', 'trending', 'rank'],
    catchup=False,
    doc_md=__doc__,
)

with dag:
    list_task = PythonOperator(
        task_id='list_trending_objects',
        python_callable=list_trending_objects,
        provide_context=True,
    )
    transform_task = PythonOperator(
        task_id='transform_trending_to_silver',
        python_callable=transform_trending_to_silver,
        provide_context=True,
    )
    list_task >> transform_task

if __name__ == "__main__":
    dag.cli()
