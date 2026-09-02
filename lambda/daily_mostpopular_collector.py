#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube 일일 수집 Lambda: videos.list(chart=mostPopular) + channels.list
- search.list를 쓰지 않는 저비용 수집 경로 (mostPopular=1 unit, channels.list=1 unit)
- EventBridge 스케줄(하루 2~3회)에 의해 트리거됨
- 대상 카테고리(영화·애니메이션/자동차·차량/게임/인물·블로그)별 인기 영상 상위 50개씩 수집 후,
  등장한 채널 전체를 channels.list로 보강해서 하나의 Bronze JSON으로 S3에 적재
- 표준 라이브러리(urllib)만 사용 -> googleapiclient 없이 Lambda Layer 불필요
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_BASE = "https://www.googleapis.com/youtube/v3"

YOUTUBE_API_KEYS = [k.strip() for k in os.environ.get("YOUTUBE_API_KEYS", "").split(",") if k.strip()]
BUCKET_NAME = os.environ["BUCKET_NAME"]
CATEGORY_IDS = [c.strip() for c in os.environ.get("CATEGORY_IDS", "1,2,20,22").split(",") if c.strip()]
REGION_CODE = os.environ.get("REGION_CODE", "KR")
MAX_RESULTS = int(os.environ.get("MAX_RESULTS", "50"))

KST = timezone(timedelta(hours=9))

s3_client = None


def get_s3_client():
    global s3_client
    if s3_client is None:
        import boto3
        s3_client = boto3.client("s3")
    return s3_client


def _request(path, params, key_index):
    """단일 API 키로 GET 요청. 성공 시 (True, json dict) / quota 문제면 (False, None)."""
    query = dict(params)
    query["key"] = YOUTUBE_API_KEYS[key_index]
    url = f"{API_BASE}/{path}?{urllib.parse.urlencode(query)}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return True, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        if e.code in (403, 429) and ("quotaExceeded" in body or "dailyLimitExceeded" in body):
            return False, None
        raise RuntimeError(f"YouTube API 오류 {e.code}: {body[:300]}")


def call_with_key_rotation(path, params):
    """모든 키를 순서대로 시도. 전부 소진되면 예외 발생."""
    if not YOUTUBE_API_KEYS:
        raise RuntimeError("YOUTUBE_API_KEYS 환경변수가 비어 있습니다.")
    last_error = None
    for idx in range(len(YOUTUBE_API_KEYS)):
        try:
            ok, data = _request(path, params, idx)
            if ok:
                return data
        except RuntimeError as e:
            last_error = e
            continue
    raise RuntimeError(f"모든 API 키의 할당량이 소진되었거나 오류가 발생했습니다: {last_error}")


def fetch_most_popular(category_id):
    """카테고리별 인기 영상 상위 MAX_RESULTS개 (다음 페이지는 안 따라감 - 일일 스냅샷 목적)."""
    return call_with_key_rotation(
        "videos",
        {
            "part": "snippet,contentDetails,statistics,status",
            "chart": "mostPopular",
            "videoCategoryId": category_id,
            "regionCode": REGION_CODE,
            "maxResults": MAX_RESULTS,
        },
    )


def fetch_channels(channel_ids):
    """채널 ID 50개씩 배치로 channels.list 호출."""
    items = []
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i + 50]
        data = call_with_key_rotation(
            "channels", {"part": "snippet,statistics", "id": ",".join(batch)}
        )
        items.extend(data.get("items", []))
        time.sleep(0.2)
    return items


def upload_to_s3(payload, collected_at):
    # S3 파티션 키(dt=/hh=)는 KST(한국시간) 날짜/시간 기준으로 나눈다.
    # payload에 담기는 collected_at_utc 필드 자체는 그대로 UTC 값을 쓴다 (감사용 타임스탬프는 UTC 유지).
    collected_at_kst = collected_at.astimezone(KST)
    key = (
        f"daily/dt={collected_at_kst.strftime('%Y-%m-%d')}/"
        f"hh={collected_at_kst.strftime('%H')}/data.json"
    )
    get_s3_client().put_object(
        Bucket=BUCKET_NAME,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    return key


def lambda_handler(event, context):
    collected_at = datetime.now(timezone.utc)

    videos_by_category = {}
    all_channel_ids = set()
    for category_id in CATEGORY_IDS:
        data = fetch_most_popular(category_id)
        items = data.get("items", [])
        videos_by_category[category_id] = items
        for v in items:
            cid = v.get("snippet", {}).get("channelId")
            if cid:
                all_channel_ids.add(cid)
        time.sleep(0.2)

    channels = fetch_channels(sorted(all_channel_ids))

    payload = {
        "collected_at_utc": collected_at.isoformat(),
        "region_code": REGION_CODE,
        "category_ids": CATEGORY_IDS,
        "videos_by_category": videos_by_category,
        "channels": channels,
    }
    s3_key = upload_to_s3(payload, collected_at)

    video_count = sum(len(v) for v in videos_by_category.values())
    result = {
        "status": "success",
        "collected_at_utc": payload["collected_at_utc"],
        "video_count": video_count,
        "channel_count": len(channels),
        "s3_bucket": BUCKET_NAME,
        "s3_key": s3_key,
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    print(lambda_handler({}, None))
