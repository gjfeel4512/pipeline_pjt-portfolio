#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
outputs/bronze_collect/channels_detail.jsonl.gz (channels.list API 원본 응답)에서
channel_thumbnail_url을 뽑아서, 이미 적재되어 있는 PostgreSQL dim_channel(Silver)
테이블에 직접 채워 넣는다.

왜 이 스크립트가 필요한가:
  youtube_api_collector.py는 체크포인트 기반이라, 검색 작업(search task)이 이미 전부
  완료된 상태에서 다시 실행하면 "모든 검색 작업이 이미 완료되어 있습니다"를 출력하고
  바로 종료한다(channel 보강 단계까지 안 감). 즉 채널 썸네일을 얻으려고 API를 다시
  호출할 필요가 없다 - channels.list 원본 응답은 outputs/bronze_collect/
  channels_detail.jsonl.gz에 이미 다 수집되어 있고, 최근 커밋(ab8ae04)에서 그동안
  버리고 있던 thumbnails 필드를 꺼내 쓰도록 수집기 코드만 고친 것이다. 그래서
  API를 다시 부르는 대신, 이미 가진 원본에서 값을 뽑아 DB에 채워 넣기만 하면 된다.

사용:
  python transforms/backfill_channel_thumbnails.py

PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE 환경변수로 접속 정보를 바꿀 수 있고,
기본값은 docker-compose.yml의 로컬 개발용 값과 같다.
"""
import gzip
import json
import os

import psycopg2
import psycopg2.extras

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANNELS_DETAIL_PATH = f"{ROOT}/outputs/bronze_collect/channels_detail.jsonl.gz"


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "airflow"),
        user=os.getenv("PGUSER", "airflow"),
        password=os.getenv("PGPASSWORD", "airflow"),
    )


def load_channel_thumbnails():
    """channel_id -> 프로필 사진 URL. medium을 우선하고 없으면 default."""
    thumbnails = {}
    if not os.path.exists(CHANNELS_DETAIL_PATH):
        return thumbnails
    with gzip.open(CHANNELS_DETAIL_PATH, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            for item in d.get("items", []):
                cid = item.get("id")
                thumbs = (item.get("snippet") or {}).get("thumbnails") or {}
                url = (thumbs.get("medium") or thumbs.get("default") or {}).get("url")
                if cid and url:
                    thumbnails[cid] = url
    return thumbnails


def main():
    thumbnails = load_channel_thumbnails()
    print(f"channels_detail.jsonl.gz 에서 프로필 사진 URL {len(thumbnails)}개 확보")
    if not thumbnails:
        print("채울 값이 없어서 종료합니다.")
        return

    conn = get_conn()
    updated = 0
    matched = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path TO youtube_analytics, public;")
            cur.execute("SELECT channel_id FROM dim_channel;")
            existing_ids = {row[0] for row in cur.fetchall()}

            for cid, url in thumbnails.items():
                if cid not in existing_ids:
                    continue
                matched += 1
                cur.execute(
                    """
                    UPDATE dim_channel
                    SET channel_thumbnail_url = %s, updated_at_utc = NOW()
                    WHERE channel_id = %s
                      AND (channel_thumbnail_url IS NULL OR channel_thumbnail_url = %s)
                    """,
                    (url, cid, ""),
                )
                updated += cur.rowcount
        conn.commit()
    finally:
        conn.close()

    print(f"dim_channel에 존재하는 channel_id와 매칭: {matched}건")
    print(f"channel_thumbnail_url 채워 넣은(업데이트된) 행: {updated}건")
    print("DONE")


if __name__ == "__main__":
    main()
