#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lambda/dashboard_refresh.py
-----------------------------
frontend/scripts/export_athena_for_dashboard.py(Athena -> outputs/silver_gold_export/*.json)
+ frontend/scripts/build_dashboard_data.py(그 json -> frontend/mock/*.json)를 하나로 합쳐
Lambda 안에서 완결시키는 버전. 두 스크립트는 "로컬 PC에서 파일로 갈아끼우는" 수동 절차를
전제로 짜였는데, infra/pipeline_orchestrator.tf의 마스터 Step Functions가 Bronze -> Silver
-> Gold가 전부 끝난 뒤 이 Lambda를 마지막 Task로 호출해서, 중간 파일 없이 Athena 쿼리
결과를 바로 frontend S3 버킷의 mock/*.json으로 쓰고 CloudFront 캐시(/mock/*)를
무효화한다.

VIDEO_ANALYSIS_CTE는 gold_compute_athena.py 것을 그대로 import해서 쓴다(복붙 금지 -
두 Lambda가 lambda/ 폴더 전체를 하나의 zip으로 묶어 배포되므로 같은 zip 안의 형제
모듈로 import 가능. infra/pipeline_orchestrator.tf의 archive_file.dashboard_refresh가
source_dir로 lambda/ 전체를 패키징하는 이유).

build_dashboard_data.py와의 차이 (의도적으로 이식하지 않은 부분):
  - Bronze 채널 아바타 폴백(outputs/bronze_collect/channels_detail.jsonl.gz)은 이식하지
    않았다. Lambda에는 그 로컬 파일이 없고, 2026-09-03 수집기 수정 이후로는 신규 채널이
    Silver(dim_channel.channel_thumbnail_url)에 이미 값을 채워 넣으므로, 그 이전에 수집된
    일부 오래된 채널만 프로필 사진이 비어 보일 수 있다(치명적이지 않음 - 프론트엔드가
    channel_avatar_url이 null이면 기본 아이콘을 보여줌).
  - "심화분석" 3종(metadata_impact/synthetic_demo/topic_trends)은 대상이 아니다 - statsmodels/
    scikit-learn 의존성이 커서 Lambda Layer 없이는 배포가 안 되고, 자동화 대상도 아니었다
    (수동 실행 스크립트로 남겨둠).

환경변수:
  ATHENA_DATABASE              Glue 데이터베이스명
  ATHENA_OUTPUT_LOCATION       Athena 쿼리 결과 저장용 S3 경로 (gold_compute_athena와 공유 가능)
  FRONTEND_BUCKET_NAME         정적 호스팅 S3 버킷 (infra/frontend.tf의 aws_s3_bucket.frontend)
  CLOUDFRONT_DISTRIBUTION_ID   캐시 무효화 대상 CloudFront 배포 ID
"""
import datetime
import json
import logging
import os
import statistics
import time
import uuid

import boto3

from gold_compute_athena import VIDEO_ANALYSIS_CTE  # noqa: E402  (같은 zip의 형제 모듈)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ATHENA_DATABASE = os.environ.get("ATHENA_DATABASE")
ATHENA_OUTPUT_LOCATION = os.environ.get("ATHENA_OUTPUT_LOCATION")
FRONTEND_BUCKET_NAME = os.environ.get("FRONTEND_BUCKET_NAME")
CLOUDFRONT_DISTRIBUTION_ID = os.environ.get("CLOUDFRONT_DISTRIBUTION_ID")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "primary")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")

CATEGORIES = {"gaming": "20", "autos_vehicles": "2", "film_animation": "1"}
TRENDING_MAX_DAYS = 90
STEADY_MIN_DAYS = 180
KST = datetime.timezone(datetime.timedelta(hours=9))

# build_dashboard_data.py와 동일 매핑 (sql/youtube_pipeline_schema_postgresql.sql의
# vw_video_analysis 정의 기준). 하나가 바뀌면 둘 다 같이 바꿔야 한다.
DURATION_BUCKET_KO = {
    "shorts": "1분 이하",
    "1_to_5m": "1~5분",
    "5_to_10m": "5~10분",
    "10_to_20m": "10~20분",
    "20m_plus": "20분 이상",
}
SLOT_INDEX = {"dawn": 0, "morning": 1, "afternoon": 2, "evening": 3, "night": 4}

athena = boto3.client("athena", region_name=AWS_REGION)
s3 = boto3.client("s3", region_name=AWS_REGION)
cloudfront = boto3.client("cloudfront")

# ----------------------------------------------------------------------------
# Athena 헬퍼 (frontend/scripts/export_athena_for_dashboard.py와 동일 로직)
# ----------------------------------------------------------------------------
def run_query(sql, timeout_sec=120):
    resp = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": ATHENA_DATABASE},
        ResultConfiguration={"OutputLocation": ATHENA_OUTPUT_LOCATION},
        WorkGroup=ATHENA_WORKGROUP,
    )
    qid = resp["QueryExecutionId"]
    waited = 0
    while waited < timeout_sec:
        r = athena.get_query_execution(QueryExecutionId=qid)
        state = r["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            return qid
        if state in ("FAILED", "CANCELLED"):
            reason = r["QueryExecution"]["Status"].get("StateChangeReason", "unknown")
            raise RuntimeError(f"Athena query {state}: {reason}\nSQL(앞 300자): {sql[:300]}")
        time.sleep(2)
        waited += 2
    raise TimeoutError(f"Athena query timed out after {timeout_sec}s (QueryExecutionId={qid})")


def fetch_all_rows(qid):
    rows = []
    cols = None
    paginator = athena.get_paginator("get_query_results")
    for page in paginator.paginate(QueryExecutionId=qid):
        for row in page["ResultSet"]["Rows"]:
            values = [c.get("VarCharValue") for c in row["Data"]]
            if cols is None:
                cols = values
                continue
            rows.append(dict(zip(cols, values)))
    return rows


def coerce_types(rows, int_fields=(), float_fields=(), bool_fields=()):
    for row in rows:
        for f in int_fields:
            if row.get(f) not in (None, ""):
                row[f] = int(float(row[f]))
        for f in float_fields:
            if row.get(f) not in (None, ""):
                row[f] = float(row[f])
        for f in bool_fields:
            if row.get(f) not in (None, ""):
                row[f] = row[f].lower() == "true"
    return rows


def fetch_video_analysis(cat_id):
    sql = VIDEO_ANALYSIS_CTE.format(db=ATHENA_DATABASE) + f"""
SELECT video_id, channel_id, category_id, title, duration_seconds, video_type,
       view_count, like_count, comment_count, subscriber_count_at_collection,
       published_day_of_week, published_hour_kst,
       video_age_days, views_per_day, like_rate, comment_rate,
       subscriber_segment, duration_bucket, upload_time_bucket
FROM video_analysis
WHERE category_id = '{cat_id}'
"""
    rows = fetch_all_rows(run_query(sql))
    return coerce_types(
        rows,
        int_fields=("duration_seconds", "view_count", "like_count", "comment_count",
                    "subscriber_count_at_collection",
                    "published_day_of_week", "published_hour_kst", "video_age_days"),
        float_fields=("views_per_day", "like_rate", "comment_rate"),
    )


def fetch_dim_channel():
    # 컬럼명을 Postgres dim_channel과 맞춘다 - normalize_silver_row()가
    # channel_title/channel_view_count/channel_video_count로 읽는데 Glue
    # silver_youtube 원본 컬럼명은 channel_name/channel_total_view_count/
    # channel_total_video_count라서 그대로 두면 channel_pool이 통째로 빈다.
    sql = f"""
SELECT channel_id, channel_name AS channel_title, subscriber_count, channel_thumbnail_url,
       channel_total_view_count AS channel_view_count,
       channel_total_video_count AS channel_video_count,
       channel_published_at_utc
FROM (
    SELECT channel_id, channel_name, subscriber_count, channel_thumbnail_url,
           channel_total_view_count, channel_total_video_count, channel_published_at_utc,
           ROW_NUMBER() OVER (PARTITION BY channel_id ORDER BY collected_at_utc DESC) AS rn
    FROM {ATHENA_DATABASE}.silver_youtube
    WHERE is_valid = true
)
WHERE rn = 1
"""
    rows = fetch_all_rows(run_query(sql))
    rows = coerce_types(rows, int_fields=("subscriber_count", "channel_view_count", "channel_video_count"))
    return {r["channel_id"]: r for r in rows if r.get("channel_id")}


def fetch_gold_category_benchmark():
    sql = f"SELECT * FROM {ATHENA_DATABASE}.gold_category_benchmark ORDER BY analysis_week DESC"
    rows = fetch_all_rows(run_query(sql))
    return coerce_types(
        rows,
        int_fields=("sample_video_count", "sample_channel_count"),
        float_fields=("median_duration_seconds", "median_views_per_day", "median_like_rate"),
    )


# ----------------------------------------------------------------------------
# frontend/scripts/build_dashboard_data.py 핵심 로직 이식 (파일 I/O 대신 메모리에서
# Athena 조회 결과를 바로 변환). Bronze 아바타 폴백은 위 docstring 설명대로 제외.
# ----------------------------------------------------------------------------
def normalize_silver_row(d, channels):
    cid = d.get("channel_id", "")
    ch = channels.get(cid, {})
    dow = d.get("published_day_of_week")  # Silver: 1=월 ... 7=일
    day_idx = (dow - 1) if isinstance(dow, int) else None
    slot_idx = SLOT_INDEX.get(d.get("upload_time_bucket"))
    age_days = d.get("video_age_days")
    views_per_day = d.get("views_per_day")
    if day_idx is None or slot_idx is None or age_days is None or views_per_day is None:
        return None

    subscriber_count = ch.get("subscriber_count")
    if subscriber_count is None:
        subscriber_count = d.get("subscriber_count_at_collection") or 0

    return {
        "video_id": d.get("video_id"),
        "title": d.get("title", ""),
        "channel_id": cid,
        "channel_name": ch.get("channel_title") or cid,
        "subscriber_count": subscriber_count,
        "channel_view_count": ch.get("channel_view_count") or 0,
        "channel_video_count": ch.get("channel_video_count") or 0,
        "channel_thumbnail_url": ch.get("channel_thumbnail_url") or None,
        "view_count": d.get("view_count") or 0,
        "like_count": d.get("like_count") or 0,
        "comment_count": d.get("comment_count") or 0,
        "duration_sec": d.get("duration_seconds") or 0,
        "duration_bucket_ko": DURATION_BUCKET_KO.get(d.get("duration_bucket"), "20분 이상"),
        "days_since_published": age_days,
        "views_per_day": float(views_per_day),
        "engagement_rate": float(d.get("like_rate") or 0),
        "day_of_week": day_idx,
        "slot": slot_idx,
    }


def build_category_trend(videos, gold_rows):
    latest = gold_rows[0] if gold_rows else None
    prev = gold_rows[1] if len(gold_rows) > 1 else None

    if latest is not None:
        avg_vpd = latest.get("median_views_per_day")
        avg_eng = latest.get("median_like_rate")
        avg_dur = latest.get("median_duration_seconds")
        avg_vpd = round(float(avg_vpd), 1) if avg_vpd is not None else None
        avg_eng = round(float(avg_eng), 4) if avg_eng is not None else None
        avg_dur = round(float(avg_dur)) if avg_dur is not None else None
        sample_size = latest.get("sample_video_count")
    elif videos:
        avg_vpd = round(statistics.median([v["views_per_day"] for v in videos]), 1)
        avg_eng = round(statistics.median([v["engagement_rate"] for v in videos]), 4)
        avg_dur = round(statistics.median([v["duration_sec"] for v in videos]))
        sample_size = len(videos)
    else:
        return None

    avg_vpd_prev = prev.get("median_views_per_day") if prev else None
    avg_eng_prev = prev.get("median_like_rate") if prev else None
    avg_vpd_prev = round(float(avg_vpd_prev), 1) if avg_vpd_prev is not None else None
    avg_eng_prev = round(float(avg_eng_prev), 4) if avg_eng_prev is not None else None

    dist, tiers = [], []
    if videos:
        buckets = {}
        for v in videos:
            buckets[v["duration_bucket_ko"]] = buckets.get(v["duration_bucket_ko"], 0) + 1
        order = ["1분 이하", "1~5분", "5~10분", "10~20분", "20분 이상"]
        dist = [
            {"label": b, "pct": round(buckets[b] / len(videos) * 100, 1)}
            for b in order if buckets.get(b)
        ]

        tiers_def = [("소형 (1만~10만)", 10_000, 100_000),
                     ("중형 (10만~50만)", 100_000, 500_000),
                     ("대형 (50만 이상)", 500_000, float("inf"))]
        vps_all = [v["view_count"] / v["subscriber_count"] for v in videos if v["subscriber_count"] > 0]
        overall_med = statistics.median(vps_all) if vps_all else None
        for label, lo, hi in tiers_def:
            vals = [v["view_count"] / v["subscriber_count"] for v in videos
                    if lo <= v["subscriber_count"] < hi]
            if vals and overall_med:
                tiers.append({"label": label, "ratio": round(statistics.median(vals) / overall_med, 1)})

    return {
        "avg_views_per_day": avg_vpd,
        "avg_views_per_day_prev": avg_vpd_prev,
        "avg_engagement_rate": avg_eng,
        "avg_engagement_rate_prev": avg_eng_prev,
        "avg_duration_sec": avg_dur,
        "duration_distribution": dist,
        "subscriber_tiers": tiers,
        "sample_size": sample_size,
    }


def build_heatmap(videos):
    cells = []
    for day in range(7):
        for slot in range(5):
            group = [v for v in videos if v["day_of_week"] == day and v["slot"] == slot]
            if group:
                cells.append({
                    "day": day, "slot": slot,
                    "avg_views": round(statistics.median([v["views_per_day"] for v in group]), 1),
                    "avg_duration_sec": round(statistics.median([v["duration_sec"] for v in group])),
                    "sample_count": len(group),
                })
            else:
                cells.append({"day": day, "slot": slot, "avg_views": None, "avg_duration_sec": None, "sample_count": 0})
    return {"cells": cells}


def build_video_pool(videos):
    trending = [v for v in videos if v["days_since_published"] <= TRENDING_MAX_DAYS]
    steady = [v for v in videos if v["days_since_published"] >= STEADY_MIN_DAYS]
    trending.sort(key=lambda v: v["views_per_day"], reverse=True)
    steady.sort(key=lambda v: v["views_per_day"], reverse=True)
    pick = trending[:15] + steady[:15]
    seen, out = set(), []
    for v in pick:
        if v["video_id"] in seen:
            continue
        seen.add(v["video_id"])
        out.append({
            "video_id": v["video_id"], "title": v["title"], "channel_id": v["channel_id"],
            "channel_name": v["channel_name"], "subscriber_count": v["subscriber_count"],
            "days_since_published": v["days_since_published"], "view_count": v["view_count"],
            "like_count": v["like_count"], "duration_sec": v["duration_sec"],
            "channel_avatar_url": v.get("channel_thumbnail_url"),
        })
    return out


def build_channel_pool(videos):
    by_channel = {}
    for v in videos:
        by_channel.setdefault(v["channel_id"], []).append(v)

    rows = []
    for cid, vids in by_channel.items():
        latest = vids[0]
        if latest["channel_video_count"] <= 0 or latest["subscriber_count"] <= 0:
            continue
        avg_vpv = latest["channel_view_count"] / latest["channel_video_count"]
        avg_eng = statistics.median([v["engagement_rate"] for v in vids])
        rep = max(vids, key=lambda v: v["view_count"])
        rows.append({
            "channel_id": cid,
            "name": latest["channel_name"],
            "subscriber_count": latest["subscriber_count"],
            "avg_views_per_video": round(avg_vpv),
            "avg_engagement_rate": round(avg_eng, 4),
            "upload_freq_per_week": None,
            "avatar_url": latest.get("channel_thumbnail_url"),
            "representative_video": {
                "video_id": rep["video_id"], "title": rep["title"], "view_count": rep["view_count"]
            },
            "_sample_videos": len(vids),
        })
    rows.sort(key=lambda r: r["avg_views_per_video"], reverse=True)
    top = rows[:15]
    for r in top:
        r.pop("_sample_videos", None)
    return top


# ----------------------------------------------------------------------------
# S3 쓰기 / CloudFront 무효화
# ----------------------------------------------------------------------------
def put_mock_json(name, data):
    key = f"mock/{name}"
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    s3.put_object(
        Bucket=FRONTEND_BUCKET_NAME,
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
    )
    logger.info("mock/%s: %d bytes -> s3://%s/%s", name, len(body), FRONTEND_BUCKET_NAME, key)


def invalidate_cloudfront():
    resp = cloudfront.create_invalidation(
        DistributionId=CLOUDFRONT_DISTRIBUTION_ID,
        InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": ["/mock/*"]},
            "CallerReference": f"dashboard-refresh-{uuid.uuid4()}",
        },
    )
    invalidation_id = resp["Invalidation"]["Id"]
    logger.info("CloudFront invalidation 생성: %s (/mock/*)", invalidation_id)
    return invalidation_id


def lambda_handler(event, context):
    missing = [
        name
        for name, value in (
            ("ATHENA_DATABASE", ATHENA_DATABASE),
            ("ATHENA_OUTPUT_LOCATION", ATHENA_OUTPUT_LOCATION),
            ("FRONTEND_BUCKET_NAME", FRONTEND_BUCKET_NAME),
            ("CLOUDFRONT_DISTRIBUTION_ID", CLOUDFRONT_DISTRIBUTION_ID),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"다음 환경변수가 필요합니다: {', '.join(missing)}")

    logger.info("=" * 70)
    logger.info("Dashboard 갱신 시작")
    logger.info("=" * 70)

    channels = fetch_dim_channel()
    gold_bench_rows = fetch_gold_category_benchmark()

    trend_out, heat_out, vpool_out, cpool_out = {}, {}, {}, {}
    report = {}
    for cat_key, cat_id in CATEGORIES.items():
        raw_rows = fetch_video_analysis(cat_id)
        videos = [v for v in (normalize_silver_row(d, channels) for d in raw_rows) if v]
        gold_rows = sorted(
            (r for r in gold_bench_rows if r.get("category_id") == cat_id),
            key=lambda r: r.get("analysis_week") or "",
            reverse=True,
        )

        trend_out[cat_key] = build_category_trend(videos, gold_rows)
        heat_out[cat_key] = build_heatmap(videos)
        vpool_out[cat_key] = build_video_pool(videos)
        cpool_out[cat_key] = build_channel_pool(videos)
        report[cat_key] = {"silver_videos": len(videos), "channels": len(cpool_out[cat_key])}
        logger.info("%s: silver_videos=%d channels=%d", cat_key, len(videos), len(cpool_out[cat_key]))

    heat_out["_comment"] = (
        "day: 0=월요일..6=일요일(KST 기준 실제 게시 요일) / slot: 0=새벽 1=오전 2=오후 3=저녁 4=심야. "
        "Silver(vw_video_analysis)에서 중앙값으로 집계. sample_count가 0이면 그 구간에 표본이 없어 "
        "avg_views가 null(nodata)입니다."
    )
    vpool_out["_comment"] = (
        "Athena(Silver: silver_youtube) + Gold(gold_category_benchmark)에서 집계. 채널 프로필 사진"
        "(channel_avatar_url)은 Silver(dim_channel.channel_thumbnail_url) 기준이며, 2026-09-03 이전에 "
        "수집된 일부 채널은 비어있을 수 있습니다. 트렌드/스테디 선정은 js/recommend.js가 이 pool을 "
        "스코어링해서 결정합니다."
    )
    cpool_out["_comment"] = (
        "Athena(Silver: silver_youtube)에서 집계. avatar_url도 Silver(dim_channel.channel_thumbnail_url) "
        "기준입니다. upload_freq_per_week는 아직 계산 불가해 null(nodata)입니다."
    )

    now_kst = datetime.datetime.now(KST).isoformat()
    meta = {
        "_comment": "Silver(vw_video_analysis/dim_channel) + Gold(gold_category_benchmark) 데이터를 "
                    "Athena에서 직접 집계해서 생성 (lambda/dashboard_refresh.py, "
                    "infra/pipeline_orchestrator.tf의 마스터 Step Functions 마지막 단계). "
                    "source=pipeline_orchestrator.",
        "last_updated": now_kst,
        "collection_window": "Bronze -> Silver -> Gold가 모두 끝난 뒤 이 Lambda가 Athena에서 직접 조회한 "
                              "스냅샷 (infra/pipeline_orchestrator.tf의 DashboardRefresh Task)",
        "source": "pipeline_orchestrator",
    }

    put_mock_json("category_trend.json", trend_out)
    put_mock_json("upload_heatmap.json", heat_out)
    put_mock_json("video_pool.json", vpool_out)
    put_mock_json("channel_pool.json", cpool_out)
    put_mock_json("meta.json", meta)

    invalidation_id = invalidate_cloudfront()

    logger.info("=" * 70)
    logger.info("Dashboard 갱신 완료: %s", json.dumps(report, ensure_ascii=False))
    logger.info("=" * 70)
    return {"report": report, "cloudfront_invalidation_id": invalidation_id, "last_updated": now_kst}
