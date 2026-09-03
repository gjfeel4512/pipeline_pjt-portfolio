#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lambda/youtube_api_daily.py (증분 버전)의 영구 체크포인트를 최초 1회 채워 넣는다.

기존에 S3 Bronze(bronze/search/...)에 이미 올라가 있는 수집 결과에서 최근 며칠치
video_id 를 긁어와 known_videos 를 미리 채우고, searched_until 을 그 데이터가 끝나는
지점으로 잡아 둔다. 이렇게 하면 4시간 간격 증분 수집이 "빈약한 4시간치"로 시작하지
않고, 첫 실행이 [기존 데이터 끝 ~ now] 구간을 자동으로 이어받는다.

실행 (로컬, 사용자 AWS 자격증명):
    BUCKET_NAME=goldline-dev-bronze-xxxx python scripts/seed_search_checkpoint.py
옵션 env:
    SEED_LOOKBACK_DAYS       (기본 2)  - known_videos 에 담을 published_at 하한
    SEED_SAFETY_MARGIN_HOURS (기본 2)  - searched_until 을 데이터 최신 시각에서 이만큼 당김
    DRY_RUN=1                          - S3에 쓰지 않고 결과만 출력
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import boto3

KST = timezone(timedelta(hours=9))

BUCKET_NAME = os.environ["BUCKET_NAME"]
CHECKPOINT_KEY = "bronze/_checkpoints/search_collector_state.json"
SEARCH_PREFIX = "bronze/search/"
SEED_LOOKBACK_DAYS = int(os.environ.get("SEED_LOOKBACK_DAYS", "2"))
SEED_SAFETY_MARGIN_HOURS = int(os.environ.get("SEED_SAFETY_MARGIN_HOURS", "2"))
DRY_RUN = os.environ.get("DRY_RUN") == "1"

# youtube_api_daily.py 와 동일 매핑 (category_name 한글 라벨 -> 그대로 사용)
SLUG_TO_LABEL = {
    "film_animation": "영화_애니메이션",
    "autos_vehicles": "자동차_차량",
    "gaming": "게임",
    "people_blogs": "인물_블로그",
}


def _parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def main():
    s3 = boto3.client("s3")

    existing = None
    try:
        obj = s3.get_object(Bucket=BUCKET_NAME, Key=CHECKPOINT_KEY)
        existing = json.loads(obj["Body"].read().decode("utf-8"))
    except s3.exceptions.NoSuchKey:
        pass
    except Exception as e:  # noqa: BLE001 - 안내만 하고 진행
        print(f"기존 체크포인트 확인 중 오류(무시하고 진행): {e}")
    if existing:
        print(
            f"주의: 체크포인트가 이미 있음 "
            f"(known_videos={len(existing.get('known_videos', {}))}, "
            f"searched_until={existing.get('searched_until')}). 덮어씁니다."
        )

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=SEED_LOOKBACK_DAYS)

    known_videos = {}          # video_id -> category_label
    latest_published = None    # 전체(필터 전) 최신 published_at
    scanned_files = 0
    scanned_rows = 0

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=SEARCH_PREFIX):
        for item in page.get("Contents", []):
            key = item["Key"]
            if not key.endswith(".jsonl"):
                continue
            slug = next((s for s in SLUG_TO_LABEL if f"/category={s}/" in key), None)
            label = SLUG_TO_LABEL.get(slug, "인물_블로그")
            body = s3.get_object(Bucket=BUCKET_NAME, Key=key)["Body"].read().decode("utf-8")
            scanned_files += 1
            for line in body.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                scanned_rows += 1
                vid = rec.get("video_id")
                if not vid:
                    continue
                pub = _parse_dt(rec.get("published_at"))
                if pub and (latest_published is None or pub > latest_published):
                    latest_published = pub
                if pub is None or pub >= cutoff:
                    known_videos.setdefault(vid, label)

    if latest_published is not None:
        searched_until_dt = (latest_published - timedelta(hours=SEED_SAFETY_MARGIN_HOURS))
    else:
        searched_until_dt = now_utc - timedelta(days=SEED_LOOKBACK_DAYS)
    searched_until = searched_until_dt.astimezone(KST).isoformat()

    state = {
        "searched_until": searched_until,
        "known_videos": dict(sorted(known_videos.items())),
    }

    print(json.dumps({
        "scanned_files": scanned_files,
        "scanned_rows": scanned_rows,
        "known_videos": len(known_videos),
        "latest_published_in_data": latest_published.isoformat() if latest_published else None,
        "searched_until": searched_until,
        "by_category": {
            lbl: sum(1 for v in known_videos.values() if v == lbl)
            for lbl in sorted(set(known_videos.values()))
        },
    }, ensure_ascii=False, indent=2))

    if not known_videos:
        print("!! known_videos 가 0개 - bronze/search/ 에 데이터가 없거나 prefix가 다릅니다. 중단.")
        sys.exit(1)

    if DRY_RUN:
        print("DRY_RUN=1 - S3에 쓰지 않음")
        return

    s3.put_object(
        Bucket=BUCKET_NAME, Key=CHECKPOINT_KEY,
        Body=json.dumps(state, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    print(f"저장 완료 -> s3://{BUCKET_NAME}/{CHECKPOINT_KEY}")


if __name__ == "__main__":
    main()
