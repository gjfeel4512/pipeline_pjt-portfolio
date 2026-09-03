#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spec.md에는 없지만 사용자가 추가로 요청한 "주제/트렌드" 분석 - outputs/silver/
{category}.jsonl의 태그(tags)를 갖고 카테고리별로 "주제 군집"을 자동으로 나누고,
각 주제가 최근에 더 잘 나가고 있는지(뜨는 주제)/덜 나가고 있는지(저무는 주제)를
실제 데이터로 계산한다. 합성 데이터 아님 - 100% 실측 Silver 데이터 기반.

방법
1. 영상의 tags 리스트를 "이미 완성된 구(phrase)"로 취급해서(단어 단위로 더 안
   쪼갬 - 한글은 띄어쓰기 기준으로 쪼개면 의미가 깨지는 경우가 많음, 예: "PC 게임
   추천"을 "PC"/"게임"/"추천"으로 쪼개면 원래 태그의 의미가 사라짐) TF-IDF 벡터화.
   태그가 하나도 없는 영상은 군집 대상에서 제외(지어내지 않음).
2. TF-IDF 벡터를 KMeans(k=6)로 군집화 → 군집마다 중심에서 가중치가 높은 태그
   5개를 뽑아 "자동 추출 주제 라벨"로 사용(사람이 이름 붙인 게 아니라 알고리즘이
   뽑은 키워드라는 걸 프론트에도 명시).
3. "트렌드"는 카테고리 내 전체 영상을 게시일 중앙값 기준으로 절반(이전/최근)으로
   나눈 뒤, 각 군집의 일평균 조회수(views_per_day) 중앙값이 이전 절반 대비 최근
   절반에서 얼마나 변했는지(%)로 계산. 한쪽 절반의 표본이 10건 미만인 군집은
   trend_pct를 null로 둠(표본 부족을 지어내지 않음).

CLAUDE.md 규칙("조회수 지표는 log 변환 후 통계 처리")은 여기서는 회귀가 아니라
군집별 중앙값 비교(로버스트 통계)라서 log 변환 대신 중앙값을 씀 - 이상치(바이럴
영상 한두 개)에 평균보다 덜 흔들리기 때문.

실행: repo 루트 어디서든 `python frontend/scripts/build_topic_trends.py`
(outputs/silver/*.jsonl이 없으면 build_silver_from_bronze_local.py 먼저 실행)
"""
import json
import os

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.dirname(SCRIPT_DIR)
ROOT = os.path.dirname(FRONTEND_DIR)
SILVER_DIR = f"{ROOT}/outputs/silver"
OUT_PATH = f"{FRONTEND_DIR}/mock/topic_trends.json"

CATEGORIES = {"gaming": "게임", "autos_vehicles": "자동차·차량", "film_animation": "영화·애니메이션"}
N_CLUSTERS = 6
MIN_HALF_SAMPLE = 10  # 이 값보다 표본이 적은 절반은 trend_pct를 null 처리


def load_category(cat_key):
    path = f"{SILVER_DIR}/{cat_key}.jsonl"
    rows = []
    if not os.path.exists(path):
        return pd.DataFrame()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if not d.get("is_valid"):
                continue
            tags = [t.strip() for t in (d.get("tags") or []) if t and t.strip()]
            if not tags:
                continue
            try:
                published = pd.Timestamp(d["published_at_utc"])
                collected = pd.Timestamp(d["collected_at_utc"])
            except (KeyError, ValueError, TypeError):
                continue
            age_days = max(1, (collected - published).total_seconds() / 86400)
            view_count = d.get("view_count") or 0
            if view_count <= 0:
                continue
            rows.append({
                "video_id": d.get("video_id"),
                "title": d.get("title") or "",
                "tags": tags,
                "published_at": published,
                "age_days": age_days,
                "views_per_day": view_count / age_days,
            })
    return pd.DataFrame(rows)


def cluster_category(df):
    vectorizer = TfidfVectorizer(
        tokenizer=lambda tags: tags,
        preprocessor=lambda x: x,
        token_pattern=None,
        lowercase=False,
        min_df=2,
        max_features=2000,
    )
    X = vectorizer.fit_transform(df["tags"])
    vocab = np.array(vectorizer.get_feature_names_out())

    k = min(N_CLUSTERS, max(2, len(df) // 200))
    km = KMeans(n_clusters=k, n_init=10, random_state=42)
    labels = km.fit_predict(X)
    df = df.copy()
    df["cluster"] = labels

    # views_per_day는 게시 후 경과일로 나눈 값이라 "최근에 올라온 영상일수록 아직
    # 초기 조회 몰림 구간이라 값이 구조적으로 부풀어 보이는" 편향이 있다(실측 확인:
    # gaming 기준 오래된 절반 중앙값 vpd=44.6인데 최근 절반은 206.5 - 4배 이상 차이나지만
    # 누적 조회수 자체는 오히려 최근 절반이 더 낮았음 - 즉 진짜 인기가 아니라 "아직 안
    # 식어서" 생기는 착시). 그래서 나이대별(연령 10분위) 또래 대비 상대값으로 정규화한
    # 뒤에 트렌드를 비교해야 진짜 "요즘 잘 나가는 주제"를 볼 수 있다.
    df["age_bucket"] = pd.qcut(df["age_days"], q=min(10, df["age_days"].nunique()), duplicates="drop")
    bucket_median = df.groupby("age_bucket", observed=True)["views_per_day"].transform("median")
    df["norm_vpd"] = df["views_per_day"] / bucket_median

    median_date = df["published_at"].median()
    older = df[df["published_at"] < median_date]
    recent = df[df["published_at"] >= median_date]

    clusters = []
    for c in range(k):
        sub = df[df["cluster"] == c]
        if sub.empty:
            continue
        centroid = km.cluster_centers_[c]
        top_idx = centroid.argsort()[::-1][:5]
        top_terms = [vocab[i] for i in top_idx if centroid[i] > 0]

        older_sub = older[older["cluster"] == c]
        recent_sub = recent[recent["cluster"] == c]
        trend_pct = None
        if len(older_sub) >= MIN_HALF_SAMPLE and len(recent_sub) >= MIN_HALF_SAMPLE:
            older_med = older_sub["norm_vpd"].median()
            recent_med = recent_sub["norm_vpd"].median()
            if older_med > 0:
                trend_pct = round(float((recent_med - older_med) / older_med * 100), 1)

        clusters.append({
            "cluster_id": int(c),
            "top_terms": top_terms,
            "sample_count": int(len(sub)),
            "median_views_per_day": round(float(sub["views_per_day"].median()), 1),
            "trend_pct": trend_pct,
            "older_sample": int(len(older_sub)),
            "recent_sample": int(len(recent_sub)),
        })

    clusters.sort(key=lambda c: c["median_views_per_day"], reverse=True)
    return {
        "sample_size": int(len(df)),
        "median_split_date": median_date.strftime("%Y-%m-%d"),
        "clusters": clusters,
    }


def main():
    out = {}
    report = []
    for cat_key, cat_name in CATEGORIES.items():
        df = load_category(cat_key)
        if len(df) < 100:
            out[cat_key] = None
            report.append(f"{cat_key}({cat_name}): sample={len(df)} < 100, skipped(nodata)")
            continue
        result = cluster_category(df)
        out[cat_key] = result
        summary = ", ".join(
            f"[{'/'.join(c['top_terms'][:3])}] n={c['sample_count']} trend={c['trend_pct']}"
            for c in result["clusters"]
        )
        report.append(f"{cat_key}({cat_name}): n={result['sample_size']} clusters: {summary}")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    out["_comment"] = (
        "outputs/silver/{category}.jsonl의 태그를 TF-IDF+KMeans(k<=6)로 군집화해 '주제'를 "
        "자동 추출(사람이 이름 붙인 카테고리 아님, 군집 중심에서 가중치 높은 태그 5개가 "
        "top_terms). trend_pct는 게시일 중앙값으로 나눈 이전/최근 절반의 '같은 나이대 "
        "또래 대비 상대 성과(norm_vpd)' 중앙값 변화율(%) - 양수면 최근에 더 잘 나가는 "
        "주제, 음수면 예전보다 덜 나가는 주제. (주의: 처음엔 나이 보정 없이 raw "
        "views_per_day로 계산했다가 모든 군집에서 +200~1400%라는 비현실적인 값이 나와서 "
        "검증해보니, 최근 영상일수록 아직 초기 조회 몰림 구간이라 값이 구조적으로 부풀어 "
        "보이는 편향임을 확인 - 나이대 10분위 또래 중앙값 대비 정규화한 뒤 비교하도록 "
        "수정함.) median_views_per_day(막대 길이용 랭킹)는 정규화 전 원값. 절반 표본이 "
        "10건 미만이면 trend_pct=null(nodata, 지어내지 않음). 합성 데이터 아님, 실측 "
        "Silver 데이터 100% 사용."
    )
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n".join(report))
    print(f"-> {OUT_PATH}")
    print("DONE")


if __name__ == "__main__":
    main()
