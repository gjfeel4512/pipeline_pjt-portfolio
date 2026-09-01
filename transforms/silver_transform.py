#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Silver Layer Transformation
Bronze(원본) → Silver(정제/가공)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple
import re
import logging
from pathlib import Path
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

REQUIRED_FIELDS = [
    'video_id', 'channel_id', 'title',
    'published_at', 'view_count', 'category_id'
]

VIDEO_TYPE_THRESHOLDS = {
    'short': (0, 240),
    'medium': (240, 1200),
    'long': (1200, float('inf'))
}


def parse_iso8601_duration(duration_str: str) -> int:
    """ISO 8601 duration을 초 단위로 변환"""
    if not duration_str or pd.isna(duration_str):
        return None

    try:
        pattern = r'P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?'
        match = re.match(pattern, str(duration_str))

        if not match:
            logger.warning(f"Invalid duration format: {duration_str}")
            return None

        years, months, days, hours, minutes, seconds = match.groups()

        total_seconds = 0
        if days:
            total_seconds += int(days) * 86400
        if hours:
            total_seconds += int(hours) * 3600
        if minutes:
            total_seconds += int(minutes) * 60
        if seconds:
            total_seconds += int(float(seconds))

        return total_seconds
    except Exception as e:
        logger.error(f"Error parsing duration '{duration_str}': {e}")
        return None


def classify_video_type(duration_seconds: int) -> str:
    """비디오 길이를 타입으로 분류"""
    if duration_seconds is None or pd.isna(duration_seconds):
        return 'unknown'

    for video_type, (min_sec, max_sec) in VIDEO_TYPE_THRESHOLDS.items():
        if min_sec <= duration_seconds < max_sec:
            return video_type

    return 'unknown'


def convert_to_kst(utc_datetime: str) -> Tuple[str, str]:
    """UTC 시간을 KST로 변환"""
    if not utc_datetime or pd.isna(utc_datetime):
        return None, None

    try:
        dt_utc = pd.to_datetime(utc_datetime, utc=True)
        dt_kst = dt_utc.astimezone(KST)

        kst_datetime = dt_kst.strftime('%Y-%m-%d %H:%M:%S%z')
        kst_datetime = kst_datetime[:-2] + ':' + kst_datetime[-2:]
        kst_date = dt_kst.strftime('%Y-%m-%d')

        return kst_datetime, kst_date
    except Exception as e:
        logger.error(f"Error converting UTC datetime '{utc_datetime}': {e}")
        return None, None


def safe_int_convert(value) -> int:
    """안전하게 정수로 변환"""
    if value is None or pd.isna(value):
        return None

    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def calculate_engagement_rate(view_count: int, like_count: int, comment_count: int) -> float:
    """engagement_rate 계산"""
    if not view_count or view_count <= 0:
        return 0.0

    like_count = like_count or 0
    comment_count = comment_count or 0

    return round((like_count + comment_count) / view_count, 6)


def validate_record(row: pd.Series) -> bool:
    """레코드 검증"""
    for field in REQUIRED_FIELDS:
        if field not in row or pd.isna(row[field]):
            return False

    if row.get('view_count', 0) < 0 or row.get('like_count', 0) < 0 or row.get('comment_count', 0) < 0:
        return False

    return True


def transform_to_silver(df: pd.DataFrame) -> pd.DataFrame:
    """Bronze 데이터를 Silver로 변환"""
    logger.info(f"Starting transformation. Input rows: {len(df)}")

    silver_df = df.copy()

    # 데이터 정제
    silver_df['view_count'] = silver_df['view_count'].apply(safe_int_convert)
    silver_df['like_count'] = silver_df['like_count'].apply(safe_int_convert)
    silver_df['comment_count'] = silver_df['comment_count'].apply(safe_int_convert)
    silver_df['subscriber_count'] = silver_df['subscriber_count'].apply(safe_int_convert)
    silver_df['channel_total_view_count'] = silver_df['channel_total_view_count'].apply(safe_int_convert)
    silver_df['channel_total_video_count'] = silver_df['channel_total_video_count'].apply(safe_int_convert)

    # duration_seconds
    silver_df['duration_seconds'] = silver_df['duration'].apply(parse_iso8601_duration)

    # video_type
    silver_df['video_type'] = silver_df['duration_seconds'].apply(classify_video_type)

    # KST 시간 변환
    silver_df[['published_at_kst', 'published_date_kst']] = silver_df['published_at'].apply(
        lambda x: pd.Series(convert_to_kst(x))
    )

    silver_df[['channel_published_at_kst', 'channel_published_date_kst']] = silver_df['channel_published_at'].apply(
        lambda x: pd.Series(convert_to_kst(x))
    )

    # 발행 년월
    silver_df['published_year_month'] = pd.to_datetime(silver_df['published_at'], utc=True).dt.strftime('%Y-%m')
    silver_df['published_year'] = pd.to_datetime(silver_df['published_at'], utc=True).dt.year
    silver_df['published_month'] = pd.to_datetime(silver_df['published_at'], utc=True).dt.month
    silver_df['published_day'] = pd.to_datetime(silver_df['published_at'], utc=True).dt.day
    silver_df['published_weekday'] = pd.to_datetime(silver_df['published_at'], utc=True).dt.day_name()

    # engagement_rate
    silver_df['engagement_rate'] = silver_df.apply(
        lambda row: calculate_engagement_rate(
            row['view_count'],
            row['like_count'],
            row['comment_count']
        ), axis=1
    )

    # 검증
    silver_df['is_valid'] = silver_df.apply(validate_record, axis=1)

    invalid_count = (~silver_df['is_valid']).sum()
    if invalid_count > 0:
        logger.warning(f"Found {invalid_count} invalid records out of {len(silver_df)}")

    logger.info(f"Transformation complete. Output rows: {len(silver_df)}")
    return silver_df


def transform_jsonl_file(input_path: str, output_path: str) -> Dict:
    """Bronze JSONL 파일을 읽어 Silver로 변환 후 저장"""
    logger.info(f"Processing: {input_path}")

    try:
        df = pd.read_json(input_path, lines=True)
        logger.info(f"Loaded {len(df)} records from {input_path}")

        silver_df = transform_to_silver(df)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            for _, row in silver_df.iterrows():
                json.dump(row.to_dict(), f, ensure_ascii=False)
                f.write('\n')

        logger.info(f"Saved {len(silver_df)} records to {output_path}")

        return {
            'status': 'success',
            'input_file': input_path,
            'output_file': output_path,
            'total_records': len(silver_df),
            'valid_records': silver_df['is_valid'].sum(),
            'invalid_records': (~silver_df['is_valid']).sum(),
        }

    except Exception as e:
        logger.error(f"Error processing {input_path}: {e}")
        return {
            'status': 'error',
            'input_file': input_path,
            'error': str(e),
        }


if __name__ == '__main__':
    print("Silver transform module loaded successfully")
