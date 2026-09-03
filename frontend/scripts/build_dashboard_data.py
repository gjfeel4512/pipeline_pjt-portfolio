#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
outputs/silver_gold_export/*.json (Silver/Gold를 export_pg_for_dashboard.py(PostgreSQL 경로)
또는 export_athena_for_dashboard.py(Athena/Glue 경로)로 내려받은 결과 - 둘 다 같은 파일명/스키마로
씀)를 읽어서 frontend/mock/*.json 을 만든다.

Bronze(outputs/bronze_merged)는 더 이상 읽지 않는다. 채널 프로필 사진도 이제
Silver(dim_channel.channel_thumbnail_url)에서 우선 가져온다 — 2026-09-03
youtube_api_collector.py 커밋(ab8ae04)부터 channels.list의 snippet.thumbnails를
더 이상 버리지 않고 Bronze에 저장하고, 그게 Silver 적재 시 dim_channel까지 그대로
들어가기 때문이다. 다만 그 커밋 이전에 수집된 채널은 아직 dim_channel에 값이
비어있을 수 있어서, 그런 채널만 예외적으로 Bronze 원본
(outputs/bronze_collect/channels_detail.jsonl.gz, channels.list API 원본 응답)으로
보완한다 — transforms/backfill_channel_thumbnails.py를 먼저 돌려서 이미 적재된
dim_channel 행들에 값을 채워 넣었다면 이 보완 경로를 탈 일은 거의 없어진다.

사전 준비 (한 번):
  1) docker-compose up -d postgres 등으로 PostgreSQL 기동
  2) sql/youtube_pipeline_schema_postgresql.sql 적용
  3) transforms/load_silver_to_postgres.py 로 Silver 적재
  4) sql/compute_gold.sql 실행 (Gold 채우기) — 안 했어도 동작은 하지만, 이 경우
     category_trend는 Silver 영상 단위에서 직접 중앙값을 재계산해 대신 채운다
     (Gold 집계값이 더 정확하므로 있으면 그쪽을 우선한다).
  5) python frontend/scripts/export_pg_for_dashboard.py 로 outputs/silver_gold_export/*.json 생성

실행: repo 루트 어디서든 `python frontend/scripts/build_dashboard_data.py`
(스크립트 자신의 경로를 기준으로 outputs/, frontend/mock/ 을 찾는다.)

outputs/silver_gold_export/ 가 아예 없거나 특정 카테고리 파일이 비어있으면, 그
카테고리는 전부 nodata로 채워진다(에러로 죽지 않는다) — export 스크립트를 아직
안 돌렸거나 Silver/Gold에 그 카테고리 데이터가 없다는 뜻이다.
"""
import glob, json, statistics, datetime, os, gzip

# 이 스크립트는 <repo_root>/frontend/scripts/ 에 있다고 가정하고, 그 위치를 기준으로
# 경로를 잡는다 (사용자 이름/절대경로에 의존하지 않아 팀원 누구나 그대로 실행 가능).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.dirname(SCRIPT_DIR)
ROOT = os.path.dirname(FRONTEND_DIR)
SILVER_GOLD_DIR = f"{ROOT}/outputs/silver_gold_export"
OUT_DIR = f"{FRONTEND_DIR}/mock"

CATEGORIES = {
    "gaming": "20",
    "autos_vehicles": "2",
    "film_animation": "1",
}
DAY_LABELS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
SLOT_LABELS = ["새벽", "오전", "오후", "저녁", "심야"]
TRENDING_MAX_DAYS = 90
STEADY_MIN_DAYS = 180
KST = datetime.timezone(datetime.timedelta(hours=9))

# vw_video_analysis(Silver)의 duration_bucket/upload_time_bucket 값(영문 코드)을
# 대시보드가 쓰는 한글 라벨/슬롯 인덱스로 매핑. 경계값은 SQL과 완전히 동일하다
# (sql/youtube_pipeline_schema_postgresql.sql의 vw_video_analysis 정의 참고).
DURATION_BUCKET_KO = {
    "shorts": "1분 이하",
    "1_to_5m": "1~5분",
    "5_to_10m": "5~10분",
    "10_to_20m": "10~20분",
    "20m_plus": "20분 이상",
}
SLOT_INDEX = {"dawn": 0, "morning": 1, "afternoon": 2, "evening": 3, "night": 4}


def load_json(name, default):
    path = f"{SILVER_GOLD_DIR}/{name}"
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default


def load_channel_avatars():
    """outputs/bronze_collect/channels_detail.jsonl.gz (channels.list 원본 응답)에서
    channel_id -> 프로필 사진 URL 맵을 만든다. Silver(dim_channel.channel_thumbnail_url)에
    아직 값이 없는 채널(2026-09-03 수집기 수정 이전에 적재된 채널)에 대한 보완용
    폴백으로만 쓴다 — normalize_silver_row/build_video_pool/build_channel_pool 참고."""
    path = f"{ROOT}/outputs/bronze_collect/channels_detail.jsonl.gz"
    avatars = {}
    if not os.path.exists(path):
        return avatars
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            for item in d.get("items", []):
                cid = item.get("id")
                thumbs = (item.get("snippet") or {}).get("thumbnails") or {}
                url = (thumbs.get("medium") or thumbs.get("default") or {}).get("url")
                if cid and url:
                    avatars[cid] = url
    return avatars


def load_dim_channel():
    """Silver dim_channel.json -> channel_id 로 바로 찾을 수 있는 dict."""
    rows = load_json("dim_channel.json", [])
    return {r["channel_id"]: r for r in rows if r.get("channel_id")}


def normalize_silver_row(d, channels):
    """vw_video_analysis(Silver) 한 행을, 아래 build_* 함수들이 쓰던 기존 필드
    이름으로 맞춰준다. 버킷/시간대 계산은 SQL이 이미 다 해놨으므로 여기서는
    이름만 맞추고 채널 정보(dim_channel)를 조인한다."""
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
        # 2026-09-03부터 Silver(dim_channel.channel_thumbnail_url)에 실제로 값이 들어옴
        # (youtube_api_collector.py 커밋 ab8ae04, channels.list snippet.thumbnails를
        # 더 이상 버리지 않음). 그 이전에 수집된 채널은 아직 비어있을 수 있어서,
        # 그런 경우에만 build_video_pool/build_channel_pool에서 Bronze 폴백을 쓴다.
        "channel_thumbnail_url": ch.get("channel_thumbnail_url") or None,
        "view_count": d.get("view_count") or 0,
        "like_count": d.get("like_count") or 0,
        "comment_count": d.get("comment_count") or 0,
        "duration_sec": d.get("duration_seconds") or 0,
        "duration_bucket_ko": DURATION_BUCKET_KO.get(d.get("duration_bucket"), "20분 이상"),
        "days_since_published": age_days,
        "views_per_day": float(views_per_day),
        # Gold(gold_category_benchmark.median_like_rate)와 같은 정의: 좋아요/조회수.
        "engagement_rate": float(d.get("like_rate") or 0),
        "day_of_week": day_idx,
        "slot": slot_idx,
    }


def load_silver_videos(cat_key, channels):
    rows = load_json(f"video_analysis_{cat_key}.json", [])
    out = []
    for d in rows:
        v = normalize_silver_row(d, channels)
        if v:
            out.append(v)
    return out


def load_gold_category_benchmark(cat_id):
    rows = load_json("gold_category_benchmark.json", [])
    rows = [r for r in rows if r.get("category_id") == cat_id]
    rows.sort(key=lambda r: r.get("analysis_week") or "", reverse=True)
    return rows


def build_category_trend(videos, gold_rows):
    """category_trend는 Gold(gold_category_benchmark)가 있으면 그 중앙값을 그대로
    쓰고(팀 SQL이 이미 계산해둔 값이라 가장 신뢰도가 높음), 없으면 Silver 영상
    단위에서 같은 방식(중앙값)으로 직접 계산한다. duration_distribution/
    subscriber_tiers는 Gold에 없는 값이라 항상 Silver에서 계산한다."""
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


def build_video_pool(videos, avatars):
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
            # Silver(dim_channel) 우선, 아직 안 채워진 채널만 Bronze 원본으로 보완.
            "channel_avatar_url": v.get("channel_thumbnail_url") or avatars.get(v["channel_id"]),
        })
    return out


def build_channel_pool(videos, avatars):
    by_channel = {}
    for v in videos:
        by_channel.setdefault(v["channel_id"], []).append(v)

    rows = []
    for cid, vids in by_channel.items():
        latest = vids[0]  # dim_channel 조인 값이라 어떤 영상을 골라도 동일함
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
            # Silver(dim_channel) 우선, 아직 안 채워진 채널만 Bronze 원본으로 보완.
            "avatar_url": latest.get("channel_thumbnail_url") or avatars.get(cid),
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


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    avatars = load_channel_avatars()
    channels = load_dim_channel()
    n_silver_thumb = sum(1 for c in channels.values() if c.get("channel_thumbnail_url"))
    trend_out, heat_out, vpool_out, cpool_out = {}, {}, {}, {}
    report = [
        f"dim_channel(Silver) loaded: {len(channels)} (channel_thumbnail_url 있는 채널: {n_silver_thumb})",
        f"channel avatars(Bronze 폴백용) loaded: {len(avatars)}",
    ]
    if not channels:
        report.append(
            "WARNING: outputs/silver_gold_export/dim_channel.json 이 없거나 비어있습니다. "
            "frontend/scripts/export_pg_for_dashboard.py 를 먼저 실행했는지, Silver 적재가 됐는지 확인하세요. "
            "(이 상태로는 모든 카테고리가 nodata로 채워집니다.)"
        )

    for cat_key, cat_id in CATEGORIES.items():
        videos = load_silver_videos(cat_key, channels)
        gold_rows = load_gold_category_benchmark(cat_id)
        report.append(f"{cat_key}: silver_videos={len(videos)} gold_benchmark_weeks={len(gold_rows)}")

        trend_out[cat_key] = build_category_trend(videos, gold_rows)
        heat_out[cat_key] = build_heatmap(videos)
        vpool_out[cat_key] = build_video_pool(videos, avatars)
        cpool_out[cat_key] = build_channel_pool(videos, avatars)

        n_trend = len([v for v in videos if v["days_since_published"] <= TRENDING_MAX_DAYS])
        n_steady = len([v for v in videos if v["days_since_published"] >= STEADY_MIN_DAYS])
        report.append(f"  trending-eligible={n_trend} steady-eligible={n_steady} channels={len(cpool_out[cat_key])}")

    with open(f"{OUT_DIR}/category_trend.json", "w", encoding="utf-8") as f:
        json.dump(trend_out, f, ensure_ascii=False, indent=2)
    with open(f"{OUT_DIR}/upload_heatmap.json", "w", encoding="utf-8") as f:
        heat_out["_comment"] = "day: 0=월요일..6=일요일(KST 기준 실제 게시 요일) / slot: 0=새벽 1=오전 2=오후 3=저녁 4=심야. Silver(vw_video_analysis)에서 중앙값으로 집계. sample_count가 0이면 그 구간에 표본이 없어 avg_views가 null(nodata)입니다."
        json.dump(heat_out, f, ensure_ascii=False, indent=2)
    with open(f"{OUT_DIR}/video_pool.json", "w", encoding="utf-8") as f:
        vpool_out["_comment"] = "PostgreSQL Silver(vw_video_analysis+dim_channel)에서 집계. 채널 프로필 사진(channel_avatar_url)도 이제 Silver(dim_channel.channel_thumbnail_url) 우선이고, 아직 안 채워진 일부 채널만 Bronze 원본으로 보완합니다. 트렌드/스테디 선정은 js/recommend.js가 이 pool을 스코어링해서 결정합니다."
        json.dump(vpool_out, f, ensure_ascii=False, indent=2)
    with open(f"{OUT_DIR}/channel_pool.json", "w", encoding="utf-8") as f:
        cpool_out["_comment"] = "PostgreSQL Silver(vw_video_analysis+dim_channel)에서 집계. avatar_url도 이제 Silver(dim_channel.channel_thumbnail_url) 우선이고, 아직 안 채워진 일부 채널만 Bronze 원본으로 보완합니다. upload_freq_per_week는 아직 계산 불가해 null(nodata)입니다."
        json.dump(cpool_out, f, ensure_ascii=False, indent=2)

    now_kst = datetime.datetime.now(KST).isoformat()
    meta = {
        "_comment": "Silver(vw_video_analysis/dim_channel) + Gold(gold_category_benchmark) 데이터를 집계해서 생성. "
                    "채널 프로필 사진도 이제 Silver(dim_channel.channel_thumbnail_url) 우선이고, 아직 안 채워진 일부 채널만 "
                    "Bronze 원본(channels_detail.jsonl.gz)으로 보완. source=pipeline_snapshot.",
        "last_updated": now_kst,
        "collection_window": "outputs/silver_gold_export 스냅샷 (export_pg_for_dashboard.py 또는 export_athena_for_dashboard.py "
                              "실행 시점의 Silver/Gold - PostgreSQL/RDS 경로와 Athena/Glue 경로 둘 다 이 파일들을 같은 스키마로 채운다)",
        "source": "pipeline_snapshot",
    }
    with open(f"{OUT_DIR}/meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("\n".join(report))
    print("DONE")


if __name__ == "__main__":
    main()
