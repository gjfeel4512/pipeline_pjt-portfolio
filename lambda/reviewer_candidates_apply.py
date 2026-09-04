#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lambda/reviewer_candidates_apply.py
----------------------------------------
reviewer_candidate_review 상태머신의 세 번째(마지막) Task - 사람이
scripts/reviewer_review_submit.py로 보낸 결정(decisions)을 받아서, 승인된 것만
채널 재분류 오버라이드 스토어(overrides/channel_category_override.json)에 반영한다.

2026-09-04 설계 결정(범위): 여기서 하는 일은 "저장"까지다. Silver/Gold 계산 로직
(lambda/gold_compute_athena.py, lambda/stepfn_transform_search_silver.py 등)이 이
오버라이드 파일을 실제로 읽어서 category_id를 바꿔치기하도록 반영하는 건 이 Lambda의
범위 밖 - 별도 작업으로 남겨둔다(Silver 재처리 시점에 적용할지, Gold 집계 쿼리에서
LEFT JOIN으로 덮어쓸지는 팀 논의 필요). 지금은 승인된 재분류 결과를 감사 가능한
형태로 durable하게 쌓아두는 것까지만 한다 - 검증 없이 바로 Gold 통계에 편입하면
오히려 품질을 해칠 수 있다는 build_cross_category_reviewers.py 원래 설계 원칙을
그대로 이어받음.

입력(event): WaitForReview Task가 SendTaskSuccess로 받은 output 중 필요한 부분만
infra/reviewer_candidates_review.tf가 Parameters로 골라 넘긴다 -
  {"batch_id": ..., "decisions": [{"channel_id", "channel_name", "approved",
   "category", "category_label", "matched_video_count"}, ...]}

환경변수:
  GOLD_BUCKET_NAME  candidates.json / overrides 스토어가 있는 S3 버킷
"""
import datetime
import json
import os

import boto3
from botocore.exceptions import ClientError

GOLD_BUCKET_NAME = os.environ.get("GOLD_BUCKET_NAME")

REVIEW_PREFIX_ROOT = "review/cross_category_reviewers"
OVERRIDE_KEY = "overrides/channel_category_override.json"
REVIEWED_KEY = "review/reviewed_channels.json"

s3 = boto3.client("s3")


def load_overrides():
    try:
        body = s3.get_object(Bucket=GOLD_BUCKET_NAME, Key=OVERRIDE_KEY)["Body"].read()
        return json.loads(body)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return []
        raise


def load_reviewed():
    """이미 검토(승인/거부)된 channel_id -> 레코드. 다음 배치 생성 시(및 대시보드에서)
    같은 채널을 다시 후보로 올리지 않기 위한 최소 원장 - 원본 Silver reject 데이터는
    건드리지 않고, "이미 결정됨" 여부만 별도로 durable하게 남긴다."""
    try:
        body = s3.get_object(Bucket=GOLD_BUCKET_NAME, Key=REVIEWED_KEY)["Body"].read()
        return json.loads(body)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return []
        raise


def lambda_handler(event, context):
    batch_id = event["batch_id"]
    decisions = event.get("decisions") or []

    approved = [d for d in decisions if d.get("approved")]
    rejected = [d for d in decisions if not d.get("approved")]
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    overrides = load_overrides()
    by_channel = {o["channel_id"]: o for o in overrides}
    for d in approved:
        by_channel[d["channel_id"]] = {
            "channel_id": d["channel_id"],
            "channel_name": d.get("channel_name"),
            "original_category_id": "22",
            "override_category": d["category"],
            "override_category_label": d.get("category_label"),
            "matched_video_count": d.get("matched_video_count"),
            "source_batch_id": batch_id,
            "approved_at_utc": now,
        }
    merged = list(by_channel.values())
    s3.put_object(
        Bucket=GOLD_BUCKET_NAME,
        Key=OVERRIDE_KEY,
        Body=json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8"),
    )

    # 이번 배치에서 결정된 채널(승인/거부 모두)을 원장에 upsert - 다음
    # GenerateCandidates 실행과 대시보드 "리뷰어 후보" 탭이 여기서 걸러낸다.
    reviewed = load_reviewed()
    reviewed_by_channel = {r["channel_id"]: r for r in reviewed}
    for d in decisions:
        reviewed_by_channel[d["channel_id"]] = {
            "channel_id": d["channel_id"],
            "channel_name": d.get("channel_name"),
            "status": "approved" if d.get("approved") else "rejected",
            "category": d.get("category"),
            "category_label": d.get("category_label"),
            "source_batch_id": batch_id,
            "decided_at_utc": now,
        }
    reviewed_merged = list(reviewed_by_channel.values())
    s3.put_object(
        Bucket=GOLD_BUCKET_NAME,
        Key=REVIEWED_KEY,
        Body=json.dumps(reviewed_merged, ensure_ascii=False, indent=2).encode("utf-8"),
    )

    # 감사 로그: 이번 배치에서 사람이 실제로 무엇을 승인/거부했는지 그대로 남긴다
    # (승인 안 된 것도 포함 - "왜 이 채널은 반영 안 됐는지" 나중에 추적 가능하게).
    decisions_key = f"{REVIEW_PREFIX_ROOT}/{batch_id}/decisions.json"
    s3.put_object(
        Bucket=GOLD_BUCKET_NAME,
        Key=decisions_key,
        Body=json.dumps(
            {"batch_id": batch_id, "decided_at_utc": now, "decisions": decisions},
            ensure_ascii=False, indent=2,
        ).encode("utf-8"),
    )

    # 검토가 끝났으니 콜백 토큰이 든 review_session.json은 지운다 - 더 쓸 일 없고,
    # 이미 소비된 토큰이 S3에 계속 남아있을 이유가 없다.
    try:
        s3.delete_object(Bucket=GOLD_BUCKET_NAME, Key=f"{REVIEW_PREFIX_ROOT}/{batch_id}/review_session.json")
    except ClientError:
        pass

    return {
        "batch_id": batch_id,
        "approved_count": len(approved),
        "rejected_count": len(rejected),
        "override_total_count": len(merged),
        "reviewed_total_count": len(reviewed_merged),
    }
