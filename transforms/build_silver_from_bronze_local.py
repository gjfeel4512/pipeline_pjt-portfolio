#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
outputs/bronze_merged/*.jsonl -> outputs/silver/*.jsonl 로 로컬 변환한다.

dags/bronze_to_silver_dag_aws.py 의 transform_to_silver()/transform_jsonl_file()/
validate_record() 로직을 그대로 복제했다 (그 파일은 최상단에서 `from airflow import
DAG` 등을 import하기 때문에 Airflow 설치 없이는 로컬에서 바로 실행할 수 없어서,
로직만 옮겨왔다). 나중에 원본 DAG의 변환 로직이 바뀌면 이 파일도 같이 맞춰줘야
한다는 점 참고.

이걸 실행하는 이유: transforms/load_silver_to_postgres.py가 기대하는 Silver 레코드
필드(published_at_utc, duration_iso8601, channel_thumbnail_url 등)는
transforms/silver_transform.py(pandas 버전)가 아니라 이 dags/bronze_to_silver_dag_aws.py
쪽 스키마와 맞다 — 실제 UPSERT_DIM_CHANNEL/UPSERT_FACT SQL의 %(...)s 자리표시자들과
1:1로 대조해서 확인함.

사용:
  python transforms/build_silver_from_bronze_local.py

outputs/bronze_merged/{gaming,autos_vehicles,film_animation}_*.jsonl 을 전부 읽어서
outputs/silver/{category}.jsonl 로 저장한다 (오염 카테고리 22, 필수 필드 누락,
음수 통계값 등은 제외한 유효 레코드만). 그 다음:

  python transforms/load_silver_to_postgres.py --local-path outputs/silver

로 로컬 PostgreSQL(youtube_analytics 스키마, 사전에 sql/youtube_pipeline_schema_postgresql.sql
적용 필요)에 적재하면 된다.
"""
import glob
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRONZE_DIR = f"{ROOT}/outputs/bronze_merged"
SILVER_DIR = f"{ROOT}/outputs/silver"

# dags/bronze_to_silver_dag_aws.py 와 동일
CATEGORY_ID_MAP = {
    '1': 'film_animation',
    '2': 'autos_vehicles',
    '20': 'gaming',
    '22': 'people_blogs',
}
CONTAMINATED_CATEGORY_IDS = {'22'}


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


def convert_to_kst(utc_datetime_str):
    if not utc_datetime_str:
        return None
    try:
        dt = datetime.fromisoformat(utc_datetime_str.replace('Z', '+00:00'))
        kst = timezone(timedelta(hours=9))
        return dt.astimezone(kst).isoformat()
    except Exception as e:
        logger.warning(f"Error converting to KST: {e}")
        return None


def safe_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def get_video_type(duration_seconds):
    """수집 시 사용한 기준과 동일: short<4분, medium 4~20분, long>20분."""
    if duration_seconds is None:
        return 'unknown'
    if duration_seconds < 240:
        return 'short'
    elif duration_seconds <= 1200:
        return 'medium'
    return 'long'


def validate_record(record):
    required_fields = ['video_id', 'channel_id', 'published_at', 'title']
    if not all(field in record and record[field] not in (None, '') for field in required_fields):
        return False
    for count_field in ('view_count', 'like_count', 'comment_count'):
        val = safe_int(record.get(count_field))
        if val is not None and val < 0:
            return False
    # category_id=22(인물·블로그)는 업로드 시 카테고리 미선택 시 YouTube 기본값이라
    # 실제 콘텐츠 성격을 신뢰할 수 없어 오염 데이터로 분리한다.
    if str(record.get('category_id')) in CONTAMINATED_CATEGORY_IDS:
        return False
    return True


def transform_to_silver(record):
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
            'privacy_status': record.get('privacy_status'),
            'live_broadcast_content': record.get('live_broadcast_content'),
            'is_live_content': record.get('live_broadcast_content') not in (None, 'none'),
            'published_at_utc': published_at,
            'published_at_kst': convert_to_kst(published_at),
            'published_year_month': published_at[:7] if published_at else None,
            'channel_published_at_utc': record.get('channel_published_at'),
            'subscriber_count': safe_int(record.get('subscriber_count')),
            'hidden_subscriber_count': bool(record.get('hidden_subscriber_count', False)),
            'channel_total_view_count': safe_int(record.get('channel_total_view_count')),
            'channel_total_video_count': safe_int(record.get('channel_total_video_count')),
            # 이 파이프라인(youtube_api_collector.py 백필)의 Bronze는 uploads_playlist_id/
            # channel_thumbnail_url을 수집하지 않아 항상 None (dim_channel 스키마를
            # 맞추기 위해 필드만 유지 — 채널 사진은 build_dashboard_data.py가 별도로
            # outputs/bronze_collect/channels_detail.jsonl.gz에서 채워 넣는다).
            'uploads_playlist_id': record.get('uploads_playlist_id') or None,
            'channel_thumbnail_url': record.get('channel_thumbnail_url') or None,
            'collected_at_utc': record.get('collected_at_utc'),
            'is_valid': validate_record(record),
            'silver_transformed_at_utc': datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Error transforming record {record.get('video_id')}: {e}")
        return None


def main():
    os.makedirs(SILVER_DIR, exist_ok=True)
    report = []
    for cat_id, cat_key in CATEGORY_ID_MAP.items():
        if cat_id == '22':
            continue  # 오염 카테고리는 애초에 수집 대상이 아님(정상 3개 카테고리만 처리)
        files = sorted(glob.glob(f"{BRONZE_DIR}/{cat_key}_*.jsonl"))
        valid, rejected, errors = 0, 0, 0
        out_path = f"{SILVER_DIR}/{cat_key}.jsonl"
        with open(out_path, "w", encoding="utf-8") as out:
            for fp in files:
                with open(fp, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            errors += 1
                            continue
                        silver_record = transform_to_silver(record)
                        if silver_record is None:
                            errors += 1
                            continue
                        if silver_record["is_valid"]:
                            out.write(json.dumps(silver_record, ensure_ascii=False) + "\n")
                            valid += 1
                        else:
                            rejected += 1
        report.append(f"{cat_key}: bronze_files={len(files)} valid={valid} rejected={rejected} errors={errors} -> {out_path}")

    print("\n".join(report))
    print("DONE")


if __name__ == "__main__":
    main()
