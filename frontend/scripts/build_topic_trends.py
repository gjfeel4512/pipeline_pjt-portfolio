#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spec.md에는 없지만 사용자가 추가로 요청한 "주제/트렌드" 분석 - outputs/silver/
{category}.jsonl의 태그(tags)를 갖고 카테고리별로 "요즘 뜨는 주제" top 5를 뽑고,
각 주제가 최근에 더 잘 나가고 있는지(뜨는 주제)/덜 나가고 있는지(저무는 주제)를
실제 데이터로 계산한다. 합성 데이터 아님 - 100% 실측 Silver 데이터 기반.

2026-09-07 4차 수정 - 방식을 통째로 바꿨다(이전엔 TF-IDF+KMeans로 태그를
"군집(cluster)"으로 묶어서 군집마다 대표 태그 여러 개를 " · "로 이어붙여 하나의
막대로 보여줬는데, 사용자 피드백 2가지를 반영해 아래 방식으로 교체함 - AND/OR
관계 없이 서로 다른 단계라 순서만 지키면 됨):
  1. ["채널ID당 중복되는 태그는 1개로"] 한 채널이 영상을 많이 올리면서 같은
     태그를 반복해서 다는 것까지 태그 인기도에 다 반영하면, 사실 그 채널 혼자
     밀어붙인 것뿐인데 "여러 사람이 같이 얘기하는 뜨는 주제"처럼 보이는 착시가
     생긴다. 그래서 태그의 "인기도"는 (그 태그가 붙은) 영상 수가 아니라 그 태그를
     쓴 서로 다른 채널 수로 센다 - 한 채널이 그 태그를 100개 영상에 달았어도
     1표로만 센다(채널 ID 기준 중복 제거).
  2. ["여러 태그를 묶지 말고 하나씩"] 군집화(KMeans)를 없애고, 위 채널 수 기준
     랭킹으로 카테고리별 상위 5개 "개별 태그"를 그대로 보여준다(TOP_N_TAGS). 부수
     효과로, 자기 채널명 성 태그가 (아래 _strip_channel_promo_tags 필터를 어쩌다
     통과하더라도) 보통 그 채널 혼자만 쓰기 때문에 채널 수가 1에 가까워서 top 5
     안에 들어오기 어려워진다 - 채널 수 기준 랭킹 자체가 자기 홍보성 태그에 대한
     2중 방어막 역할도 하는 셈.

적용 순서(우선순위):
  0단계: video_id 중복 스냅샷 제거 + LOOKBACK_DAYS 이내로 제한(load_category)
  1단계: 자기 채널명(또는 그 일부)으로 보이는 태그를 영상별로 제거
         (_strip_channel_promo_tags - 완전 일치는 무조건, 부분 일치는 채널 수
         조건까지 봄 - 자세한 조건은 함수 docstring 참고)
  2단계: 남은 태그를 "서로 다른 채널 수"로 랭킹해 상위 5개만 채택(rank_top_tags)
  3단계: 채택된 태그마다 트렌드(%)/일평균 조회수 계산, 막대 정렬은
         median_views_per_day(일평균 조회수) 내림차순으로 다시 정렬 - "얼마나
         널리 퍼졌나"로 뽑고 "얼마나 잘 나가나"로 보여주는 순서.

방법
1. 영상의 tags 리스트를 "이미 완성된 구(phrase)"로 취급(단어 단위로 더 안 쪼갬 -
   한글은 띄어쓰기 기준으로 쪼개면 의미가 깨지는 경우가 많음). 태그가 하나도 없는
   영상은 대상에서 제외(지어내지 않음).
2. 위 "적용 순서" 1~2단계로 걸러진 태그들 중 카테고리별 상위 5개만 채택.
3. "트렌드"는 카테고리 내 전체 영상을 게시일 중앙값 기준으로 절반(이전/최근)으로
   나눈 뒤, 각 태그가 달린 영상들의 일평균 조회수(views_per_day) 중앙값이 이전
   절반 대비 최근 절반에서 얼마나 변했는지(%)로 계산. 한쪽 절반의 표본이 10건
   미만인 태그는 trend_pct를 null로 둠(표본 부족을 지어내지 않음).

CLAUDE.md 규칙("조회수 지표는 log 변환 후 통계 처리")은 여기서는 회귀가 아니라
태그별 중앙값 비교(로버스트 통계)라서 log 변환 대신 중앙값을 씀 - 이상치(바이럴
영상 한두 개)에 평균보다 덜 흔들리기 때문.

실행: repo 루트 어디서든 `python frontend/scripts/build_topic_trends.py`
(outputs/silver/*.jsonl이 없으면 build_silver_from_bronze_local.py 먼저 실행)
"""
import json
import os
import re
from collections import defaultdict

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.dirname(SCRIPT_DIR)
ROOT = os.path.dirname(FRONTEND_DIR)
SILVER_DIR = f"{ROOT}/outputs/silver"
OUT_PATH = f"{FRONTEND_DIR}/mock/topic_trends.json"

CATEGORIES = {"gaming": "게임", "autos_vehicles": "자동차·차량", "film_animation": "영화·애니메이션"}
TOP_N_TAGS = 5  # 카테고리당 보여줄 "요즘 뜨는 주제(태그)" 개수
MIN_HALF_SAMPLE = 10  # 이 값보다 표본이 적은 절반은 trend_pct를 null 처리

# 2026-09-07: build_metadata_impact.py와 같은 이유로 두 가지를 같이 고친다.
# (1) outputs/silver/{category}.jsonl에 한 영상의 재수집 스냅샷이 여러 개 그대로
#     들어있는데(dedup 안 됨) 여기선 그걸 다 별도 영상처럼 집계에 넣고 있었다 -
#     오래 추적된(=인기) 영상일수록 암묵적으로 더 많은 표를 갖게 되는 문제라,
#     video_id별 최신 스냅샷 1개만 남긴다.
# (2) 데이터가 계속 쌓이면 median_split_date(예전/최근 절반 나누는 기준)가 점점
#     과거로 밀려서 "최근"의 의미가 흐려진다 - 게시일 기준 최근 LOOKBACK_DAYS
#     이내 영상만 사용해서, 항상 비슷한 폭의 "최근" 구간을 비교하게 한다.
#     (build_metadata_impact.py와 동일하게 6개월로 결정)
LOOKBACK_DAYS = 180

# 2026-09-07: 채널이 자기 채널명(또는 그 일부)을 태그로 그대로 박아넣는 경우가 많아서
# ("자기 홍보성 태그"), "주제"를 뽑을 때 사람 닉네임/채널명이 주제 키워드로 섞여
# 나오는 문제가 있었다(사용자 리포트, 실측: 태그 173,410개 중 영상 소속 채널명과
# 정규화 후 완전히 같은 태그가 2,262개(1.3%)). 다만 "채널명에 포함되면 무조건
# 제거"는 위험한데, 채널명 자체에 진짜 장르 단어가 들어있는 경우가 있기 때문이다
# (예: 채널명이 "빅핑거 [게임추천]"이면 "게임추천"이라는, 다른 수백 채널도 공통으로
# 쓰는 진짜 장르 태그까지 지워질 뻔했음). 그래서 "이 카테고리 전체에서 그 태그를
# 쓰는 서로 다른 채널이 몇 개인가"를 같이 본다 - 진짜 주제 태그는 여러 채널이
# 공통으로 쓰지만, 자기 채널명 태그는 사실상 그 채널 혼자만(또는 극소수만) 쓴다.
# "채널명과 겹치는 태그"이면서 "그 태그를 쓰는 채널 수가 SELF_TAG_MAX_CHANNELS
# 이하"인 경우에만 자기 홍보성 태그로 보고 제거한다.
SELF_TAG_MAX_CHANNELS = 1


def _normalize_tag_text(s):
    """비교용 정규화: 대소문자/공백/괄호류/흔한 채널명 접미사 제거 후 영숫자+한글만 남긴다."""
    s = (s or "").strip().lower()
    s = re.sub(r"[\[\]「」『』()（）【】]", " ", s)
    for suffix in ("youtube", "유튜브", "tv", "채널", "official", "공식"):
        s = re.sub(rf"\s*{re.escape(suffix)}\s*$", "", s)
    return re.sub(r"[^0-9a-z가-힣]", "", s)


def _strip_channel_promo_tags(df):
    """영상 태그 중 '자기 채널명(또는 그 일부)'으로 보이는 자기 홍보성 태그를 뺀다.

    - 채널명과 정규화 후 완전히 같은 태그: 다른 채널이 같은 텍스트를 태그로 쓰고
      있든 말든 무조건 제거한다(애매할 여지 없음 - "이 영상의 채널명 그 자체"라는
      것 자체가 이미 확실한 신호. 예전엔 실수로 아래 채널 수 조건을 여기에도 같이
      걸어서, 그 채널명과 우연히 같은 태그를 다른 채널도 쓰면 정작 그 채널 자신의
      자기 태그가 안 지워지는 버그가 있었음 - 사용자 리포트로 발견, 2026-09-07).
    - 채널명에 부분 포함되는 태그: 이 카테고리에서 그 태그를 쓰는 서로 다른
      채널 수가 SELF_TAG_MAX_CHANNELS 이하일 때만 제거(여러 채널이 공통으로
      쓰는 진짜 장르 태그는 채널명과 우연히 겹쳐도 보존됨). 이 "채널 수"는
      원본 태그 문자열이 아니라 정규화된 태그 기준으로 센다 - 원본 기준으로
      세면 같은 태그를 철자만 다르게 쓴 경우(대소문자/공백/괄호 차이 등) 서로
      다른 키로 쪼개져서 실제로는 여러 채널이 같이 쓰는 태그인데도 채널 수가
      낮게 잡혀 잘못 제거될 수 있다(2026-09-08, tag_count_fix 브랜치에서 발견).
    - 태그가 다 제거돼 빈 리스트가 된 영상은 이후 랭킹 대상에서 제외한다(빈 태그로
      지어내지 않음 - 파일 상단 docstring에서 밝힌 "태그 없는 영상 제외" 원칙과
      동일선상).
    - 채택된 태그는 항상 원본 문자열 그대로 남긴다(정규화는 비교/카운트에만
      쓰고, 최종 결과에 정규화된 문자열이 노출되면 안 됨 - 한 번 이 실수로
      화면에 태그가 뭉개져 나온 적이 있었음, 2026-09-07).
    """
    if df.empty:
        return df

    tag_channels = defaultdict(set)
    for _, row in df.iterrows():
        cid = row["channel_id"] or row["channel_name"]
        for t in row["tags"]:
            t_norm = _normalize_tag_text(t)
            if t_norm:
                tag_channels[t_norm].add(cid)

    def filter_row(row):
        ch_norm = _normalize_tag_text(row["channel_name"])
        if not ch_norm:
            return row["tags"]
        kept = []
        for t in row["tags"]:
            t_norm = _normalize_tag_text(t)
            if not t_norm:
                kept.append(t)
                continue
            if t_norm == ch_norm:
                continue  # 완전 일치 - 무조건 제거
            if t_norm in ch_norm and len(tag_channels[t_norm]) <= SELF_TAG_MAX_CHANNELS:
                continue  # 부분 포함 + 소수 채널만 사용 - 제거
            kept.append(t)  # 원본 문자열 유지(정규화 값 아님)
        return kept

    df = df.copy()
    df["tags"] = df.apply(filter_row, axis=1)
    return df[df["tags"].map(len) > 0]


# AWS_S3_SILVER_BUCKET이 있으면 S3에서 직접 읽는다(Lambda 자동화용,
# frontend/scripts/export_s3_for_dashboard.py의 iter_s3_silver_jsonl과 동일한
# 키 규칙). 없으면 로컬 outputs/silver/{category}.jsonl을 그대로 읽는다.
SILVER_BUCKET = os.environ.get("AWS_S3_SILVER_BUCKET")


def iter_silver_lines(cat_key):
    if SILVER_BUCKET:
        import boto3
        s3 = boto3.client("s3")
        prefix = f"youtube/silver/category={cat_key}/"
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=SILVER_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                if not obj["Key"].endswith(".jsonl"):
                    continue
                body = s3.get_object(Bucket=SILVER_BUCKET, Key=obj["Key"])["Body"].read().decode("utf-8")
                yield from body.splitlines()
    else:
        path = f"{SILVER_DIR}/{cat_key}.jsonl"
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            yield from f


def load_category(cat_key):
    rows = []
    for line in iter_silver_lines(cat_key):
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
            "collected_at_utc": d.get("collected_at_utc") or "",
            "channel_id": d.get("channel_id") or "",
            "channel_name": d.get("channel_name") or "",
            "title": d.get("title") or "",
            "tags": tags,
            "published_at": published,
            "age_days": age_days,
            "views_per_day": view_count / age_days,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # (2) 최근 LOOKBACK_DAYS 이내 게시된 영상만 사용
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=LOOKBACK_DAYS)
    df = df[df["published_at"] >= cutoff]

    # (1) video_id별로 collected_at_utc가 가장 늦은 스냅샷 1개만 남긴다
    df = df.sort_values("collected_at_utc").drop_duplicates("video_id", keep="last")
    df = df.drop(columns=["collected_at_utc"])

    # (3) 자기 채널명(또는 그 일부)을 그대로 태그로 쓴 자기 홍보성 태그 제거
    df = _strip_channel_promo_tags(df)
    # channel_id는 rank_top_tags에서 "태그당 서로 다른 채널 수"를 셀 때 써야 해서
    # 여기선 안 지우고, channel_name만 지운다(더는 쓸 데 없음).
    return df.drop(columns=["channel_name"])


def rank_top_tags(df):
    """카테고리별 상위 TOP_N_TAGS개 "개별 태그"를 뽑아 트렌드/조회수를 계산한다.

    랭킹 기준은 "그 태그를 쓴 서로 다른 채널 수"(영상 수가 아님 - 파일 상단
    docstring 1번 참고). 채택된 태그들의 화면 표시 순서(막대 길이)는
    median_views_per_day 내림차순으로 다시 정렬한다.
    """
    if df.empty:
        return {"sample_size": 0, "median_split_date": None, "clusters": []}

    tag_channels = defaultdict(set)
    tag_video_count = defaultdict(int)
    for _, row in df.iterrows():
        cid = row["channel_id"] or ""
        for t in row["tags"]:
            tag_channels[t].add(cid)
            tag_video_count[t] += 1

    # 채널 수 내림차순 -> (동률이면) 영상 수 내림차순 -> (그래도 동률이면) 태그명
    # 오름차순(결과 재현성을 위한 순서일 뿐, 의미상 우선순위는 아님).
    ranked_tags = sorted(
        tag_channels.keys(),
        key=lambda t: (-len(tag_channels[t]), -tag_video_count[t], t),
    )[:TOP_N_TAGS]

    # views_per_day는 게시 후 경과일로 나눈 값이라 "최근에 올라온 영상일수록 아직
    # 초기 조회 몰림 구간이라 값이 구조적으로 부풀어 보이는" 편향이 있다(실측 확인:
    # gaming 기준 오래된 절반 중앙값 vpd=44.6인데 최근 절반은 206.5 - 4배 이상 차이나지만
    # 누적 조회수 자체는 오히려 최근 절반이 더 낮았음 - 즉 진짜 인기가 아니라 "아직 안
    # 식어서" 생기는 착시). 그래서 나이대별(연령 10분위) 또래 대비 상대값으로 정규화한
    # 뒤에 트렌드를 비교해야 진짜 "요즘 잘 나가는 주제"를 볼 수 있다.
    df = df.copy()
    df["age_bucket"] = pd.qcut(df["age_days"], q=min(10, df["age_days"].nunique()), duplicates="drop")
    bucket_median = df.groupby("age_bucket", observed=True)["views_per_day"].transform("median")
    df["norm_vpd"] = df["views_per_day"] / bucket_median

    median_date = df["published_at"].median()
    older = df[df["published_at"] < median_date]
    recent = df[df["published_at"] >= median_date]

    items = []
    for tag in ranked_tags:
        has_tag = df["tags"].apply(lambda tags, t=tag: t in tags)
        sub = df[has_tag]
        if sub.empty:
            continue
        older_sub = older[older["tags"].apply(lambda tags, t=tag: t in tags)]
        recent_sub = recent[recent["tags"].apply(lambda tags, t=tag: t in tags)]
        trend_pct = None
        if len(older_sub) >= MIN_HALF_SAMPLE and len(recent_sub) >= MIN_HALF_SAMPLE:
            older_med = older_sub["norm_vpd"].median()
            recent_med = recent_sub["norm_vpd"].median()
            if older_med > 0:
                trend_pct = round(float((recent_med - older_med) / older_med * 100), 1)

        items.append({
            "cluster_id": len(items),  # 프론트 호환용 필드명 유지 - 실제로는 "군집"이 아니라 개별 태그 순번
            "top_terms": [tag],  # 이제 태그 1개짜리 리스트 - 프론트가 join해도 그대로 태그 하나만 나옴
            "channel_count": len(tag_channels[tag]),  # 랭킹에 쓴 값, 참고용으로 같이 노출
            "sample_count": int(len(sub)),
            "median_views_per_day": round(float(sub["views_per_day"].median()), 1),
            "trend_pct": trend_pct,
            "older_sample": int(len(older_sub)),
            "recent_sample": int(len(recent_sub)),
        })

    items.sort(key=lambda c: c["median_views_per_day"], reverse=True)
    for i, item in enumerate(items):
        item["cluster_id"] = i

    return {
        "sample_size": int(len(df)),
        "median_split_date": median_date.strftime("%Y-%m-%d"),
        "clusters": items,
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
        result = rank_top_tags(df)
        out[cat_key] = result
        summary = ", ".join(
            f"[{c['top_terms'][0]}] ch={c['channel_count']} n={c['sample_count']} trend={c['trend_pct']}"
            for c in result["clusters"]
        )
        report.append(f"{cat_key}({cat_name}): n={result['sample_size']} top{TOP_N_TAGS}: {summary}")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    out["_comment"] = (
        "outputs/silver/{category}.jsonl의 태그 중 자기 채널명(또는 그 일부)으로 보이는 "
        "자기 홍보성 태그를 제외한 뒤(_strip_channel_promo_tags), 그 태그를 쓴 서로 다른 "
        "채널 수(channel_count, 영상 수 아님 - 한 채널이 반복해서 단 태그는 1표로만 "
        "반영)로 랭킹해 카테고리별 상위 5개(TOP_N_TAGS) '개별 태그'를 뽑음(사람이 이름 "
        "붙인 카테고리 아님, 여러 태그를 묶지 않고 하나씩 노출 - top_terms는 태그 1개짜리 "
        "리스트). 화면 표시 순서(막대 길이)는 채널 수가 아니라 median_views_per_day "
        "내림차순. trend_pct는 게시일 중앙값으로 나눈 이전/최근 절반의 '같은 나이대 또래 "
        "대비 상대 성과(norm_vpd)' 중앙값 변화율(%) - 양수면 최근에 더 잘 나가는 주제, "
        "음수면 예전보다 덜 나가는 주제. (주의: 처음엔 나이 보정 없이 raw "
        "views_per_day로 계산했다가 모든 항목에서 +200~1400%라는 비현실적인 값이 나와서 "
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
