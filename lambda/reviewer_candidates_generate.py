#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lambda/reviewer_candidates_generate.py
------------------------------------------
frontend/scripts/build_cross_category_reviewers.py를 서버리스로 옮긴 것 - 로컬
outputs/silver/rejected/*.jsonl 대신 S3(youtube/silver-rejected/category=people_blogs/)를
직접 스캔한다. KEYWORDS/classify()는 build_cross_category_reviewers.py와 완전히
동일하게 유지해야 한다 - 둘 중 하나만 바뀌면 로컬 재현 결과와 이 Lambda 결과가
어긋난다.

infra/reviewer_candidates_review.tf의 reviewer_candidate_review 상태머신, 첫 번째
Task(GenerateCandidates). 후보 목록을 계산만 하고 Gold 버킷에 스냅샷(candidates.json)
으로 남긴다 - 여기서는 아무것도 확정/편입하지 않는다. 사람 검토 후 승인된 것만
reviewer_candidates_apply.py가 overrides/channel_category_override.json에 반영한다.

환경변수:
  SILVER_BUCKET_NAME  Silver S3 버킷 (rejected 레코드가 있는 곳)
  GOLD_BUCKET_NAME    후보 스냅샷(review/cross_category_reviewers/<batch_id>/candidates.json)을 쓸 S3 버킷
"""
import datetime
import json
import os
from collections import defaultdict

import boto3
from botocore.exceptions import ClientError

SILVER_BUCKET_NAME = os.environ.get("SILVER_BUCKET_NAME")
GOLD_BUCKET_NAME = os.environ.get("GOLD_BUCKET_NAME")

KST = datetime.timezone(datetime.timedelta(hours=9))
REJECTED_PREFIX = "youtube/silver-rejected/category=people_blogs/"
REVIEW_PREFIX_ROOT = "review/cross_category_reviewers"
REVIEWED_KEY = "review/reviewed_channels.json"

CATEGORY_LABELS = {"film_animation": "영화·애니메이션", "autos_vehicles": "자동차·차량", "gaming": "게임"}

# frontend/scripts/build_cross_category_reviewers.py의 KEYWORDS/classify()와 동일 -
# 둘 중 하나만 고치면 로컬 재현 결과와 이 Lambda 결과가 어긋나니 항상 같이 수정할 것.
KEYWORDS = {
    "film_animation": {
        "strong": ["박스오피스", "개봉작", "결말포함", "영화리뷰", "영화 리뷰", "애니 리뷰", "넷플릭스 영화", "영화 해석", "영화 요약"],
        "weak": ["영화", "애니메이션", "감독", "출연진", "관람평"],
    },
    "autos_vehicles": {
        "strong": ["시승기", "신차", "전기차", "중고차", "장기렌트", "차박"],
        "weak": ["자동차", "차량", "SUV", "세단", "시승"],
    },
    "gaming": {
        "strong": ["게임리뷰", "게임 리뷰", "게임 공략", "신작 게임", "스팀", "닌텐도", "플스", "PS5"],
        "weak": ["게임", "플레이", "공략", "패치"],
    },
}
MIN_STRONG = 1
MIN_WEAK = 2

s3 = boto3.client("s3")


def classify(text):
    scores = {}
    for cat, kw in KEYWORDS.items():
        strong_hits = sum(1 for k in kw["strong"] if k in text)
        weak_hits = sum(1 for k in kw["weak"] if k in text)
        if strong_hits >= MIN_STRONG or weak_hits >= MIN_WEAK:
            scores[cat] = strong_hits * 2 + weak_hits
    if not scores:
        return None
    best = max(scores.values())
    winners = [c for c, s in scores.items() if s == best]
    if len(winners) != 1:
        return None
    return winners[0], best


def iter_rejected_lines():
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=SILVER_BUCKET_NAME, Prefix=REJECTED_PREFIX):
        for obj in page.get("Contents", []):
            body = s3.get_object(Bucket=SILVER_BUCKET_NAME, Key=obj["Key"])["Body"].read().decode("utf-8")
            for line in body.splitlines():
                line = line.strip()
                if line:
                    yield line


def load_reviewed_channel_ids():
    """이미 승인/거부로 결정된 channel_id 집합 - reviewer_candidates_apply.py가 쓴다.
    다음 배치에 같은 채널을 다시 후보로 올리지 않기 위한 필터. 원장이 아직 없으면
    (첫 실행 등) 빈 집합으로 취급 - 필터링 없이 정상 진행."""
    try:
        body = s3.get_object(Bucket=GOLD_BUCKET_NAME, Key=REVIEWED_KEY)["Body"].read()
        return {r["channel_id"] for r in json.loads(body)}
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return set()
        raise


def lambda_handler(event, context):
    missing = [n for n, v in (("SILVER_BUCKET_NAME", SILVER_BUCKET_NAME), ("GOLD_BUCKET_NAME", GOLD_BUCKET_NAME)) if not v]
    if missing:
        raise RuntimeError(f"다음 환경변수가 필요합니다: {', '.join(missing)}")

    channels = defaultdict(lambda: {"video_count": 0, "channel_name": None, "score_by_cat": defaultdict(int), "sample_titles": []})
    total_rejected = 0
    total_skipped_reviewed = 0
    reviewed_ids = load_reviewed_channel_ids()

    for line in iter_rejected_lines():
        total_rejected += 1
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(d.get("category_id")) != "22":
            continue
        if d.get("channel_id") in reviewed_ids:
            total_skipped_reviewed += 1
            continue
        text = (d.get("title") or "") + " " + " ".join(d.get("tags") or [])
        result = classify(text)
        if not result:
            continue
        cat, score = result
        ch = channels[d.get("channel_id")]
        ch["channel_name"] = d.get("channel_name")
        ch["video_count"] += 1
        ch["score_by_cat"][cat] += score
        if len(ch["sample_titles"]) < 3:
            ch["sample_titles"].append(d.get("title"))

    candidates = []
    for channel_id, info in channels.items():
        best_cat = max(info["score_by_cat"], key=info["score_by_cat"].get)
        candidates.append({
            "channel_id": channel_id,
            "channel_name": info["channel_name"],
            "suggested_category": best_cat,
            "suggested_category_label": CATEGORY_LABELS[best_cat],
            "matched_video_count": info["video_count"],
            "sample_titles": info["sample_titles"],
        })
    candidates.sort(key=lambda c: c["matched_video_count"], reverse=True)

    batch_id = datetime.datetime.now(KST).strftime("batch-%Y%m%d-%H%M%S")
    candidates_key = f"{REVIEW_PREFIX_ROOT}/{batch_id}/candidates.json"
    out = {
        "batch_id": batch_id,
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total_rejected_scanned": total_rejected,
        "candidates": candidates,
    }
    s3.put_object(
        Bucket=GOLD_BUCKET_NAME,
        Key=candidates_key,
        Body=json.dumps(out, ensure_ascii=False, indent=2).encode("utf-8"),
    )

    return {
        "batch_id": batch_id,
        "candidates_key": candidates_key,
        "candidate_count": len(candidates),
        "total_rejected_scanned": total_rejected,
        "total_skipped_already_reviewed": total_skipped_reviewed,
    }
