#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step Functions 파이프라인(search_to_silver_stepfn)의 2단계 Lambda.

dags/search_bronze_to_silver_dag.py의 transform_search_to_silver() 태스크와 완전히 동일한
변환/오염 판정 기준을 재사용 - 로직을 그대로 복사해왔다(배치 파이프라인
bronze_to_silver_dag_aws.py의 transform_to_silver()/validate_record()와도 필드 매핑 동일).
Step Functions의 Map 상태가 이 Lambda를 Bronze 오브젝트 키 하나당 한 번씩,
병렬로 호출한다(오케스트레이션 계층에서만 Airflow와 다름 - 로직은 동일).

이벤트: {"key": "bronze/search/category=gaming/year=2026/month=09/day=03/gaming_....jsonl"}
반환:   {"key": ..., "valid_records": N, "rejected_records": N, "uploaded": ["youtube/silver/..."]}
"""
from datetime import datetime, timezone, timedelta
import json
import os
import re

import boto3

AWS_S3_BRONZE_BUCKET = os.environ["BRONZE_BUCKET_NAME"]
AWS_S3_SILVER_BUCKET = os.environ["SILVER_BUCKET_NAME"]

CATEGORY_ID_MAP = {
    '1':  'film_animation',
    '2':  'autos_vehicles',
    '20': 'gaming',
    '22': 'people_blogs',
}
# 22(인물·블로그)는 카테고리 오분류 리뷰어를 잡아내는 용도로만 최소 수집 - 정상 분석
# 대상이 아니므로 배치 파이프라인과 동일하게 오염 데이터로 분리
CONTAMINATED_CATEGORY_IDS = {'22'}


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
    except Exception:
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
    """dags/search_bronze_to_silver_dag.py의 transform_to_silver()와 동일한 출력 스키마"""
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

            # 발견 경로: 'search'(태그 검색) | 'trending'(인기 급상승, 구독자 대비
            # 조회수 비율 TRENDING_SUB_RATIO_MIN 이상). trending_sub_ratio는 발견
            # 시점의 조회수/구독자 비율(그 외에는 None) - Bronze 수집기에서 계산.
            'discovered_via': record.get('discovered_via', 'search'),
            'trending_sub_ratio': record.get('trending_sub_ratio'),

            'is_valid': validate_record(record),
            'silver_transformed_at_utc': datetime.utcnow().isoformat(),
            'source': 'daily_search_collector_stepfn',
        }
    except Exception as e:
        print(f"Error transforming record: {e}")
        return None


def lambda_handler(event, context):
    key = event["key"]
    s3 = boto3.client("s3")

    obj = s3.get_object(Bucket=AWS_S3_BRONZE_BUCKET, Key=key)
    lines = obj["Body"].read().decode("utf-8").splitlines()

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
            print(f"Invalid JSON in {key}: {e}")
            continue
        silver = transform_to_silver(record)
        if not silver:
            continue
        if silver["is_valid"]:
            valid_records.append(silver)
        else:
            rejected_records.append(silver)

    uploaded = []
    for records, prefix_root, fname_prefix in (
        (valid_records, 'youtube/silver', 'silver'),
        (rejected_records, 'youtube/silver-rejected', 'rejected'),
    ):
        if not records:
            continue
        fname = f"{fname_prefix}_{fname_suffix}.jsonl"
        s3_key = f"{prefix_root}/category={slug}/year={year}/month={month}/day={day}/{fname}"
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"
        s3.put_object(Bucket=AWS_S3_SILVER_BUCKET, Key=s3_key, Body=body.encode("utf-8"))
        uploaded.append(s3_key)
        print(f"Uploaded {len(records)} records -> s3://{AWS_S3_SILVER_BUCKET}/{s3_key}")

    return {
        "key": key,
        "valid_records": len(valid_records),
        "rejected_records": len(rejected_records),
        "uploaded": uploaded,
    }
