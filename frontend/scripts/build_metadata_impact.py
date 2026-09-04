#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spec.md 분석 7(메타데이터 최적화) - outputs/silver/{category}.jsonl을 읽어서
카테고리별로 영상/채널 메타데이터가 성과(일평균 조회수)에 미치는 영향을
다중회귀(OLS)로 추정하고, frontend/mock/metadata_impact.json으로 저장한다.

방법
- 종속변수: log(views_per_day) - CLAUDE.md 규칙("조회수 지표는 log 변환 후 통계
  처리")을 따름. views_per_day = view_count / max(1, 게시 후 경과일수).
- 통제변수(관심 대상 아님, 채널 규모가 다른 요인 효과를 왜곡하지 않게 통제):
  log(subscriber_count+1)
- 독립변수(관심 대상, spec.md 3-2 "축 B" 항목들 위주로 최대한 넓게 포함):
  * caption_available   - 자막 유무
  * is_weekend           - 주말(토/일) 게시 여부 - 애초에 is_hd(화질)를 넣었었는데
    3개 카테고리 전부 SD 영상이 15건 미만(<0.3%)이라 계수가 불안정해서 제외하고,
    실제로 변동이 충분한 이 변수로 교체함(실측: gaming SD=14/5431건 등)
  * tag_count            - 태그 개수 (spec.md: 태그 수)
  * title_length         - 제목 길이 (spec.md: 제목 길이)
  * description_length   - 설명 길이 (spec.md: 설명 길이)
  * link_count           - 설명란 링크 개수 (spec.md: "링크 수", description의 http(s):// 개수)
  * log_duration          - 영상 길이 (로그, "2배일 때" 해석)
  * log_channel_video_count - 채널의 누적 업로드 경험치 (로그, "2배일 때" 해석)
  * log_channel_age_days   - 채널 개설 후 이 영상 게시까지 지난 일수 (로그, "2배일 때" 해석)
- 유의성: statsmodels OLS의 p-value를 그대로 씀. p>=0.05인 계수는 프론트에서
  "근거 부족(유의하지 않음)"으로 표시하고 막대를 흐리게 처리 - 표본이 작은
  카테고리에서 지어낸 확신을 주지 않기 위함.
- has_paid_product_placement는 이번 로컬 Bronze(youtube_api_collector.py 과거 수집분)에
  아직 그 필드가 없어서(2026-09-03에 추가된 필드) 이번 회귀에서는 제외함.

실행: repo 루트 어디서든 `python frontend/scripts/build_metadata_impact.py`
(outputs/silver/*.jsonl이 없으면 frontend/scripts/build_silver_from_bronze_local.py를
먼저 실행해야 함)
"""
import json
import os
import re

import numpy as np
import pandas as pd
import statsmodels.api as sm

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.dirname(SCRIPT_DIR)
ROOT = os.path.dirname(FRONTEND_DIR)
SILVER_DIR = f"{ROOT}/outputs/silver"
OUT_PATH = f"{FRONTEND_DIR}/mock/metadata_impact.json"

# AWS_S3_SILVER_BUCKET이 있으면 S3에서 직접 읽는다(Lambda 자동화용,
# frontend/scripts/export_s3_for_dashboard.py의 iter_s3_silver_jsonl과 동일한
# 키 규칙). 없으면 로컬 outputs/silver/{category}.jsonl을 그대로 읽는다(로컬
# 개발/디버깅용 - 기존 동작 그대로 유지).
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

CATEGORIES = {"gaming": "게임", "autos_vehicles": "자동차·차량", "film_animation": "영화·애니메이션"}
URL_RE = re.compile(r"https?://")

# (내부 변수명, 프론트 표시용 한글 라벨, 해석 방식)
#   "binary": 계수를 "있음 vs 없음 %"로 환산
#   "per_unit": 계수를 "1 단위 변화당 %"로 환산
#   "per_unit_x10": 계수를 "10 단위 변화당 %"로 환산
#   "per_unit_x100": 계수를 "100 단위 변화당 %"로 환산
#   "log_double": 로그 변환 변수를 "2배가 될 때 %"로 환산
FEATURES = [
    ("caption_available", "자막 유무", "binary"),
    ("is_weekend", "주말(토/일) 게시 여부", "binary"),
    ("tag_count", "태그 개수(1개당)", "per_unit"),
    ("link_count", "설명란 링크 개수(1개당)", "per_unit"),
    ("title_length", "제목 길이(10자당)", "per_unit_x10"),
    ("description_length", "설명 길이(100자당)", "per_unit_x100"),
    ("log_duration", "영상 길이(2배일 때)", "log_double"),
    ("log_channel_video_count", "채널 누적 업로드 수(2배일 때)", "log_double"),
    ("log_channel_age_days", "채널 개설 후 경과일(2배일 때)", "log_double"),
]


def load_category(cat_key):
    rows = []
    for line in iter_silver_lines(cat_key):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        if not d.get("is_valid"):
            continue
        try:
            published = pd.Timestamp(d["published_at_utc"])
            collected = pd.Timestamp(d["collected_at_utc"])
        except (KeyError, ValueError, TypeError):
            continue
        age_days = max(1, (collected - published).total_seconds() / 86400)
        view_count = d.get("view_count") or 0
        subscriber_count = d.get("subscriber_count") or 0
        duration_seconds = d.get("duration_seconds") or 0
        channel_video_count = d.get("channel_total_video_count") or 0
        if view_count <= 0 or duration_seconds <= 0 or channel_video_count <= 0:
            continue

        channel_age_days = None
        ch_pub = d.get("channel_published_at_utc")
        if ch_pub:
            try:
                channel_age_days = max(1, (published - pd.Timestamp(ch_pub)).total_seconds() / 86400)
            except (ValueError, TypeError):
                channel_age_days = None
        if channel_age_days is None:
            continue

        try:
            dow = pd.Timestamp(d["published_at_kst"]).isoweekday()  # 1=월 ... 7=일
        except (KeyError, ValueError, TypeError):
            continue

        description = d.get("description") or ""
        rows.append({
            "views_per_day": view_count / age_days,
            "caption_available": 1 if d.get("caption_available") else 0,
            "is_weekend": 1 if dow in (6, 7) else 0,
            "tag_count": len(d.get("tags") or []),
            "title_length": len(d.get("title") or ""),
            "description_length": len(description),
            "link_count": len(URL_RE.findall(description)),
            "duration_seconds": duration_seconds,
            "channel_video_count": channel_video_count,
            "channel_age_days": channel_age_days,
            "subscriber_count": subscriber_count,
        })
    return pd.DataFrame(rows)


def fit_category(df):
    df = df.copy()
    df["log_views_per_day"] = np.log(df["views_per_day"])
    df["log_duration"] = np.log(df["duration_seconds"] + 1)
    df["log_channel_video_count"] = np.log(df["channel_video_count"] + 1)
    df["log_channel_age_days"] = np.log(df["channel_age_days"] + 1)
    df["log_subscriber"] = np.log(df["subscriber_count"] + 1)

    x_cols = [
        "caption_available", "is_weekend", "tag_count", "link_count", "title_length",
        "description_length", "log_duration", "log_channel_video_count",
        "log_channel_age_days", "log_subscriber",
    ]
    X = sm.add_constant(df[x_cols])
    y = df["log_views_per_day"]
    model = sm.OLS(y, X).fit()

    results = []
    for col, label, mode in FEATURES:
        coef = model.params[col]
        pval = model.pvalues[col]
        if mode in ("binary", "per_unit"):
            pct = (np.exp(coef) - 1) * 100
        elif mode == "per_unit_x10":
            pct = (np.exp(coef * 10) - 1) * 100
        elif mode == "per_unit_x100":
            pct = (np.exp(coef * 100) - 1) * 100
        elif mode == "log_double":
            pct = (np.exp(coef * np.log(2)) - 1) * 100
        else:
            raise ValueError(mode)
        results.append({
            "feature": col,
            "label": label,
            "effect_pct": round(float(pct), 1),
            "p_value": round(float(pval), 4),
            "significant": bool(pval < 0.05),
        })

    return {
        "sample_size": int(len(df)),
        "r_squared": round(float(model.rsquared), 4),
        "features": results,
    }


def main():
    out = {}
    report = []
    for cat_key, cat_name in CATEGORIES.items():
        df = load_category(cat_key)
        if len(df) < 30:
            out[cat_key] = None
            report.append(f"{cat_key}({cat_name}): sample={len(df)} < 30, skipped(nodata)")
            continue
        result = fit_category(df)
        out[cat_key] = result
        sig = [f["label"] for f in result["features"] if f["significant"]]
        report.append(
            f"{cat_key}({cat_name}): n={result['sample_size']} R2={result['r_squared']} significant={sig}"
        )

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    out["_comment"] = (
        "outputs/silver/{category}.jsonl에서 log(views_per_day) ~ 자막유무+주말게시여부+태그수+"
        "설명링크수+제목길이+설명길이+log(영상길이)+log(채널누적업로드수)+log(채널개설경과일)"
        "+log(구독자수, 통제) 다중회귀(OLS)로 추정. effect_pct는 각 요인 계수를 "
        "'해석 가능한 단위 변화당 조회수 %변화'로 환산한 값(라벨 참고). p_value>=0.05(significant=false)면 "
        "통계적으로 유의하지 않다는 뜻 - 프론트에서 흐리게 표시하고 확정적으로 말하지 않을 것. "
        "표본 30건 미만 카테고리는 null(nodata)."
    )
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n".join(report))
    print(f"-> {OUT_PATH}")
    print("DONE")


if __name__ == "__main__":
    main()
