#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
frontend/scripts/build_cross_category_reviewers.py
-----------------------------------------------------
category_id=22(people_blogs)는 lambda/youtube_api_daily.py의 CATEGORY_TAGS 주석대로
"업로더가 카테고리를 안 정해서 YouTube가 자동으로 붙인 값"인 경우가 많아서, 다른
카테고리(영화/자동차/게임) 리뷰인데 22로 잘못 분류된 채널을 잡아내는 탐지용으로도
같이 수집한다(검색어 자체가 "영화리뷰"/"시승기"/"게임리뷰"). 그런데 지금까지는
이렇게 격리된 outputs/silver/rejected/(S3의 youtube/silver-rejected/)를 아무도
다시 읽지 않아서, 잡아내기만 하고 전혀 활용을 안 하고 있었다.

이 스크립트는 그 최소한의 활용이다 - "자동 재분류"는 하지 않는다(검증 없이 Gold
통계에 편입하면 오히려 품질을 해칠 수 있음, matched_tags 필드도 애초에 기록 안 됨:
lambda/youtube_api_daily.py 주석 "다운스트림에서 아무도 안 씀" 참고). 대신
outputs/silver/rejected/rejected_people_blogs_*.jsonl을 제목/태그 키워드로 훑어서
"다른 카테고리 리뷰처럼 보이는" 채널을 사람이 검수할 후보 목록으로만 뽑는다.

분류 기준: 카테고리별 strong/weak 키워드 사전으로 title+tags를 스캔해서 채널
단위로 집계한다. strong 키워드 1개 이상 또는 weak 키워드 2개 이상 매칭되고,
그 카테고리 점수가 다른 카테고리와 겹치지 않을 때만(애매하면 제외) 후보로 채택
한다 - 우연히 단어 하나 겹친 걸로 확정하지 않는다는, 이 리포에서 이미 쓰고 있는
"지어내지 않는다" 원칙과 동일하게 맞춘 것.

2026-09-04: lambda/reviewer_candidates_apply.py가 (사람이 Step Functions
reviewer_candidate_review 상태머신으로 검토를 마친 뒤) S3에 남기는
review/reviewed_channels.json을 읽어서, 이미 승인/거부로 결정된 채널은 원본 reject
데이터를 지우지 않은 채로 이 스크립트의 출력(=대시보드 "리뷰어 후보" 탭)에서만
제외한다. --gold-bucket을 안 주면(또는 S3 접근이 안 되면) 이 필터링은 그냥
건너뛴다 - AWS 자격증명 없이도 로컬 전용으로 계속 쓸 수 있어야 하므로 실패로 죽지
않는다.

2026-09-07: lambda/refresh_dashboard.py가 4시간 주기 오케스트레이션의 일부로 이
스크립트를 직접 실행하도록 붙였다(전에는 아무도 자동으로 재실행하지 않아서, 사람이
리뷰어 후보를 승인/거부해도 대시보드 "리뷰어 후보" 탭에는 계속 예전 후보 목록이
그대로 남아있었다 - review/reviewed_channels.json은 갱신되는데 이 스크립트의 출력
frontend/mock/cross_category_reviewers.json은 아무도 다시 만들지 않았기 때문).
Lambda의 /tmp 환경에는 로컬 outputs/silver/rejected/*.jsonl이 없으므로,
--silver-bucket을 주면 로컬 glob 대신 S3(youtube/silver-rejected/category=people_blogs/)를
lambda/reviewer_candidates_generate.py와 동일한 방식(list_objects_v2 페이지네이션)으로
직접 스캔한다. --silver-bucket을 생략하면 기존과 동일하게 로컬 파일을 읽는다 - 로컬
개발자 워크플로(scripts/run-local.bat 등)는 전혀 바뀌지 않는다.

출력: frontend/mock/cross_category_reviewers.json
실행:
  # 로컬(기존과 동일)
  python frontend/scripts/build_cross_category_reviewers.py --gold-bucket <GOLD_BUCKET_NAME>
  (--gold-bucket을 생략하면 이미 검토한 채널 필터링 없이 예전과 동일하게 동작)

  # S3 기반(Lambda/로컬 Silver 파일이 없는 환경)
  python frontend/scripts/build_cross_category_reviewers.py \
      --gold-bucket <GOLD_BUCKET_NAME> --silver-bucket <SILVER_BUCKET_NAME>
"""
import argparse
import glob
import json
import os
from collections import defaultdict

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.dirname(SCRIPT_DIR)
ROOT = os.path.dirname(FRONTEND_DIR)
SILVER_DIR = f"{ROOT}/outputs/silver"
OUT_PATH = f"{FRONTEND_DIR}/mock/cross_category_reviewers.json"
# lambda/reviewer_candidates_apply.py가 쓰는 것과 동일한 키 - 이미 검토된 채널 목록.
REVIEWED_KEY = "review/reviewed_channels.json"
# lambda/reviewer_candidates_generate.py의 REJECTED_PREFIX와 동일 - --silver-bucket
# 스캔 시 이 prefix 밑을 페이지네이션으로 읽는다.
REJECTED_PREFIX = "youtube/silver-rejected/category=people_blogs/"

CATEGORY_LABELS = {"film_animation": "영화·애니메이션", "autos_vehicles": "자동차·차량", "gaming": "게임"}

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


def load_reviewed_channel_ids(gold_bucket):
    """이미 승인/거부로 결정된 channel_id 집합. gold_bucket이 없거나 S3에 접근할 수
    없으면(자격증명 없음 등) 필터링 없이 빈 집합을 돌려주고 경고만 출력한다 - 이
    스크립트는 AWS 없이도 계속 동작해야 한다."""
    if not gold_bucket:
        return set()
    try:
        s3 = boto3.client("s3")
        body = s3.get_object(Bucket=gold_bucket, Key=REVIEWED_KEY)["Body"].read()
        return {r["channel_id"] for r in json.loads(body)}
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return set()
        print(f"경고: {REVIEWED_KEY} 조회 실패({e}) - 이미 검토한 채널 필터링 없이 진행합니다.")
        return set()
    except NoCredentialsError:
        print("경고: AWS 자격증명이 없어 이미 검토한 채널 필터링을 건너뜁니다.")
        return set()


def _iter_local_lines():
    """기존 동작: outputs/silver/rejected/rejected_people_blogs_*.jsonl을 그대로 읽는다."""
    pattern = f"{SILVER_DIR}/rejected/rejected_people_blogs_*.jsonl"
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield line


def _iter_s3_lines(silver_bucket):
    """lambda/reviewer_candidates_generate.py의 iter_rejected_lines()와 동일한 방식
    (list_objects_v2 페이지네이션)으로 S3의 REJECTED_PREFIX 밑을 직접 읽는다. Lambda의
    /tmp 환경처럼 로컬 outputs/silver/가 없는 곳에서 이 스크립트를 돌려야 할 때 쓴다."""
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=silver_bucket, Prefix=REJECTED_PREFIX):
        for obj in page.get("Contents", []):
            body = s3.get_object(Bucket=silver_bucket, Key=obj["Key"])["Body"].read().decode("utf-8")
            for line in body.splitlines():
                line = line.strip()
                if line:
                    yield line


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--gold-bucket", default=os.environ.get("GOLD_BUCKET_NAME"),
                    help="review/reviewed_channels.json이 있는 Gold S3 버킷 (생략 시 필터링 없이 동작)")
    p.add_argument("--silver-bucket", default=None,
                    help="지정하면 로컬 outputs/silver/rejected/ 대신 S3(youtube/silver-rejected/"
                         "category=people_blogs/)를 직접 스캔한다. 로컬 Silver 파일이 없는 환경"
                         "(예: lambda/refresh_dashboard.py의 /tmp)에서 실행할 때 사용. 생략하면"
                         " 기존과 동일하게 로컬 파일을 읽는다.")
    args = p.parse_args()
    reviewed_ids = load_reviewed_channel_ids(args.gold_bucket)
    channels = defaultdict(lambda: {"video_count": 0, "channel_name": None, "score_by_cat": defaultdict(int), "sample_titles": []})
    total_rejected = 0
    total_skipped_reviewed = 0

    lines = _iter_s3_lines(args.silver_bucket) if args.silver_bucket else _iter_local_lines()
    for line in lines:
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            # 오염 데이터(malformed_json)일 수 있음 - 이 레코드만 건너뛴다.
            continue
        total_rejected += 1
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

    out = {
        "candidates": candidates,
        "total_rejected_scanned": total_rejected,
        "total_skipped_already_reviewed": total_skipped_reviewed,
        "_comment": (
            "category_id=22(인물·블로그)로 격리된 outputs/silver/rejected/rejected_people_blogs_*.jsonl을 "
            "제목/태그 키워드로 훑어서, 실제로는 영화/자동차/게임 리뷰인데 YouTube가 카테고리를 "
            "자동으로 22로 붙인 것으로 보이는 채널을 찾아낸 것. 자동으로 재분류/Gold 편입하지 않음 - "
            "검증 없이 편입하면 품질을 해칠 수 있어서, 사람이 검수할 후보 목록으로만 제공한다. "
            "strong 키워드 1개 이상 또는 weak 키워드 2개 이상 매칭 + 카테고리가 애매하지 않은 "
            "채널만 후보로 채택(우연히 한 단어 겹친 것으로 확정하지 않음)."
        ),
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"scanned {total_rejected} rejected records -> {len(candidates)} candidate channels "
          f"({total_skipped_reviewed}건은 이미 검토됨 - 제외)")
    print(f"-> {OUT_PATH}")
    print("DONE")


if __name__ == "__main__":
    main()
