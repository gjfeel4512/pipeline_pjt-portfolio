#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Silver JSONL(S3 prefix 또는 로컬 파일/디렉터리) -> PostgreSQL(youtube_analytics 스키마) 적재.

Silver 레코드 하나당:
  1) dim_date  upsert (published_date_kst 기준)
  2) dim_channel upsert
  3) fact_video_snapshot upsert (video_id + collected_date 기준)

sql/youtube_pipeline_schema_postgresql.sql이 먼저 적용되어 있어야 한다.

사용 예:
  python transforms/load_silver_to_postgres.py --s3-prefix youtube/silver/ --bucket goldline-dev-silver-827913617635
  python transforms/load_silver_to_postgres.py --local-path outputs/silver
"""
import argparse
import glob
import json
import os
import uuid
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

# Silver의 video_type("short"/"medium"/"long"/"unknown")을
# Postgres CHECK 제약(ck_snapshot_video_type)이 허용하는 값으로 매핑
VIDEO_TYPE_MAP = {"short": "shorts", "medium": "short_form", "long": "long_form"}
DOW_KO = {1: "월", 2: "화", 3: "수", 4: "목", 5: "금", 6: "토", 7: "일"}


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "airflow"),
        user=os.getenv("PGUSER", "airflow"),
        password=os.getenv("PGPASSWORD", "airflow"),
    )


def iter_local_records(path):
    files = [path] if os.path.isfile(path) else sorted(glob.glob(os.path.join(path, "**", "*.jsonl"), recursive=True))
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)


def iter_s3_records(bucket, prefix):
    import boto3
    s3 = boto3.client("s3", region_name=os.getenv("AWS_DEFAULT_REGION", "us-west-2"))
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".jsonl"):
                continue
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
            for line in body.splitlines():
                line = line.strip()
                if line:
                    yield json.loads(line)


def parse_kst(published_at_kst):
    """'2026-08-30T12:45:56+09:00' -> naive datetime(KST 벽시계 값)"""
    if not published_at_kst:
        return None
    dt = datetime.fromisoformat(published_at_kst)
    return dt.replace(tzinfo=None)


def prepare_row(rec):
    """Silver 레코드 -> fact_video_snapshot insert용 dict. 스키마 제약을 못 만족하면 None 반환."""
    if not rec.get("is_valid", False):
        return None

    video_type = VIDEO_TYPE_MAP.get(rec.get("video_type"))
    if video_type is None:
        return None  # 'unknown' 등 CHECK 제약 밖의 값은 스킵

    published_at_kst_dt = parse_kst(rec.get("published_at_kst"))
    if published_at_kst_dt is None:
        return None

    collected_at_utc = rec.get("collected_at_utc")
    if not collected_at_utc:
        return None
    collected_dt = datetime.fromisoformat(collected_at_utc.replace("Z", "+00:00"))

    category_id = rec.get("category_id")
    if category_id is None:
        return None

    return {
        "snapshot_id": str(uuid.uuid4()),
        "video_id": rec["video_id"],
        "channel_id": rec["channel_id"],
        "category_id": str(category_id),
        "published_at_utc": rec.get("published_at_utc"),
        "published_at_kst": published_at_kst_dt,
        "published_date_kst": published_at_kst_dt.date(),
        "published_hour_kst": published_at_kst_dt.hour,
        "published_day_of_week": published_at_kst_dt.isoweekday(),
        "title": rec.get("title") or "",
        "duration_iso8601": rec.get("duration_iso8601") or "",
        "duration_seconds": rec.get("duration_seconds") or 0,
        "video_type": video_type,
        "definition": rec.get("definition"),
        "caption_available": rec.get("caption_available"),
        "live_broadcast_content": rec.get("live_broadcast_content") or "none",
        "view_count": rec.get("view_count") or 0,
        "like_count": rec.get("like_count"),
        "comment_count": rec.get("comment_count"),
        "subscriber_count_at_collection": rec.get("subscriber_count"),
        "trending_rank": rec.get("trending_rank"),
        "collected_at_utc": collected_dt,
        "collected_date": collected_dt.date(),
        "is_public": (rec.get("privacy_status") == "public"),
        "is_valid": True,
        "invalid_reason": None,
    }


UPSERT_DIM_DATE = """
INSERT INTO youtube_analytics.dim_date
    (date_key, year, month, day, day_of_week_num, day_of_week_ko, is_weekend)
VALUES (%(date_key)s, %(year)s, %(month)s, %(day)s, %(dow)s, %(dow_ko)s, %(is_weekend)s)
ON CONFLICT (date_key) DO NOTHING
"""

UPSERT_DIM_CHANNEL = """
INSERT INTO youtube_analytics.dim_channel
    (channel_id, channel_title, channel_published_at, subscriber_count,
     hidden_subscriber_count, channel_view_count, channel_video_count, last_collected_at_utc)
VALUES (%(channel_id)s, %(channel_title)s, %(channel_published_at)s, %(subscriber_count)s,
        %(hidden_subscriber_count)s, %(channel_view_count)s, %(channel_video_count)s, %(last_collected_at_utc)s)
ON CONFLICT (channel_id) DO UPDATE SET
    channel_title = EXCLUDED.channel_title,
    subscriber_count = EXCLUDED.subscriber_count,
    hidden_subscriber_count = EXCLUDED.hidden_subscriber_count,
    channel_view_count = EXCLUDED.channel_view_count,
    channel_video_count = EXCLUDED.channel_video_count,
    last_collected_at_utc = EXCLUDED.last_collected_at_utc,
    updated_at_utc = NOW()
"""

UPSERT_FACT = """
INSERT INTO youtube_analytics.fact_video_snapshot
    (snapshot_id, video_id, channel_id, category_id,
     published_at_utc, published_at_kst, published_date_kst, published_hour_kst, published_day_of_week,
     title, duration_iso8601, duration_seconds, video_type, definition, caption_available,
     live_broadcast_content, view_count, like_count, comment_count, subscriber_count_at_collection,
     trending_rank, collected_at_utc, collected_date, is_public, is_valid, invalid_reason)
VALUES
    (%(snapshot_id)s, %(video_id)s, %(channel_id)s, %(category_id)s,
     %(published_at_utc)s, %(published_at_kst)s, %(published_date_kst)s, %(published_hour_kst)s, %(published_day_of_week)s,
     %(title)s, %(duration_iso8601)s, %(duration_seconds)s, %(video_type)s, %(definition)s, %(caption_available)s,
     %(live_broadcast_content)s, %(view_count)s, %(like_count)s, %(comment_count)s, %(subscriber_count_at_collection)s,
     %(trending_rank)s, %(collected_at_utc)s, %(collected_date)s, %(is_public)s, %(is_valid)s, %(invalid_reason)s)
ON CONFLICT (video_id, collected_date) DO UPDATE SET
    view_count = EXCLUDED.view_count,
    like_count = EXCLUDED.like_count,
    comment_count = EXCLUDED.comment_count,
    subscriber_count_at_collection = EXCLUDED.subscriber_count_at_collection,
    trending_rank = EXCLUDED.trending_rank,
    collected_at_utc = EXCLUDED.collected_at_utc
"""


def load(records, conn):
    stats = {"seen": 0, "skipped": 0, "loaded": 0}
    with conn.cursor() as cur:
        for rec in records:
            stats["seen"] += 1
            row = prepare_row(rec)
            if row is None:
                stats["skipped"] += 1
                continue

            d = row["published_date_kst"]
            cur.execute(UPSERT_DIM_DATE, {
                "date_key": d, "year": d.year, "month": d.month, "day": d.day,
                "dow": d.isoweekday(), "dow_ko": DOW_KO[d.isoweekday()],
                "is_weekend": d.isoweekday() in (6, 7),
            })

            cur.execute(UPSERT_DIM_CHANNEL, {
                "channel_id": row["channel_id"],
                "channel_title": rec.get("channel_name") or row["channel_id"],
                "channel_published_at": rec.get("channel_published_at_utc"),
                "subscriber_count": rec.get("subscriber_count"),
                "hidden_subscriber_count": bool(rec.get("hidden_subscriber_count", False)),
                "channel_view_count": rec.get("channel_total_view_count"),
                "channel_video_count": rec.get("channel_total_video_count"),
                "last_collected_at_utc": row["collected_at_utc"],
            })

            cur.execute(UPSERT_FACT, row)
            stats["loaded"] += 1
    conn.commit()
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--s3-prefix", help="예: youtube/silver/")
    ap.add_argument("--bucket", help="S3 버킷명 (--s3-prefix와 함께 사용)")
    ap.add_argument("--local-path", help="로컬 .jsonl 파일 또는 폴더")
    args = ap.parse_args()

    if args.local_path:
        records = iter_local_records(args.local_path)
    elif args.s3_prefix and args.bucket:
        records = iter_s3_records(args.bucket, args.s3_prefix)
    else:
        ap.error("--local-path 또는 (--s3-prefix + --bucket) 중 하나는 필요합니다.")

    conn = get_conn()
    try:
        stats = load(records, conn)
    finally:
        conn.close()

    print(f"읽음 {stats['seen']}건 / 적재 {stats['loaded']}건 / 스킵 {stats['skipped']}건")


if __name__ == "__main__":
    main()
