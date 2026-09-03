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

출력: frontend/mock/cross_category_reviewers.json
실행: repo 루트 어디서든 `python frontend/scripts/build_cross_category_reviewers.py`
"""
import glob
import json
import os
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.dirname(SCRIPT_DIR)
ROOT = os.path.dirname(FRONTEND_DIR)
SILVER_DIR = f"{ROOT}/outputs/silver"
OUT_PATH = f"{FRONTEND_DIR}/mock/cross_category_reviewers.json"

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


def main():
    channels = defaultdict(lambda: {"video_count": 0, "channel_name": None, "score_by_cat": defaultdict(int), "sample_titles": []})
    total_rejected = 0
    pattern = f"{SILVER_DIR}/rejected/rejected_people_blogs_*.jsonl"
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                total_rejected += 1
                if str(d.get("category_id")) != "22":
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
    print(f"scanned {total_rejected} rejected records -> {len(candidates)} candidate channels")
    print(f"-> {OUT_PATH}")
    print("DONE")


if __name__ == "__main__":
    main()
