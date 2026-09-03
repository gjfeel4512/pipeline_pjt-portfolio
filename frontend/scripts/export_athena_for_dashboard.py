#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
frontend/scripts/export_athena_for_dashboard.py
--------------------------------------------------
export_pg_for_dashboard.py(PostgreSQL 버전)의 Athena 버전. build_dashboard_data.py가
읽는 outputs/silver_gold_export/*.json을 PostgreSQL/RDS 없이, Athena(Glue Catalog의
silver_youtube + gold_* 테이블)만으로 만든다. 출력 파일명/스키마는 기존 스크립트와
동일하므로 build_dashboard_data.py는 전혀 수정하지 않아도 된다.

전제 조건:
  1) infra/gold_athena.tf가 배포되어 있고(terraform apply), gold_compute_athena
     Lambda가 최소 한 번 이상 실행되어 S3 Gold 버킷에 gold_category_benchmark/
     gold_upload_strategy/gold_new_creator_guide가 채워져 있어야 함
  2) 이 스크립트를 실행하는 환경(로컬 PC)에 AWS CLI 자격증명이 설정돼 있어야 함
     (aws configure, 또는 AWS_ACCESS_KEY_ID/SECRET 환경변수)
  3) Glue Catalog의 silver_youtube 파티션 프로젝션이 켜져 있어야 함(이미 적용됨)

사용:
  python frontend/scripts/export_athena_for_dashboard.py \\
      --database goldline_dev_db \\
      --gold-bucket goldline-dev-gold-<account_id> \\
      --athena-output s3://goldline-dev-gold-<account_id>/athena-query-results/

환경변수로도 지정 가능: ATHENA_DATABASE / GOLD_BUCKET_NAME / ATHENA_OUTPUT_LOCATION /
AWS_DEFAULT_REGION (기본 us-west-2)

결과: export_pg_for_dashboard.py와 동일하게 outputs/silver_gold_export/ 아래에
  video_analysis_{gaming,autos_vehicles,film_animation}.json
  dim_channel.json
  gold_category_benchmark.json / gold_upload_strategy.json / gold_new_creator_guide.json

2026-09-03: 랭크 추적(gold_video_rank_trend) 기능 자체를 폐지하여 이 스크립트는 더 이상
gold_video_rank_trend.json을 만들지 않는다(frontend 쪽에서도 이 파일을 읽는 곳이 없음).
"""
import argparse
import json
import os
import sys
import time

import boto3

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.dirname(SCRIPT_DIR)
ROOT = os.path.dirname(FRONTEND_DIR)
OUT_DIR = os.path.join(ROOT, "outputs", "silver_gold_export")

# gold_compute_athena.py의 video_analysis CTE/카테고리명을 그대로 재사용 -
# 두 곳의 로직이 벌어지지 않도록 import해서 쓴다 (복붙 금지).
sys.path.insert(0, os.path.join(ROOT, "lambda"))
from gold_compute_athena import VIDEO_ANALYSIS_CTE, CATEGORY_NAME_KO  # noqa: E402

CATEGORIES = {"gaming": "20", "autos_vehicles": "2", "film_animation": "1"}


def get_args():
    p = argparse.ArgumentParser()
    p.add_argument("--database", default=os.getenv("ATHENA_DATABASE"))
    p.add_argument("--gold-bucket", default=os.getenv("GOLD_BUCKET_NAME"))
    p.add_argument("--athena-output", default=os.getenv("ATHENA_OUTPUT_LOCATION"))
    p.add_argument("--region", default=os.getenv("AWS_DEFAULT_REGION", "us-west-2"))
    args = p.parse_args()
    missing = [n for n, v in (("--database", args.database), ("--gold-bucket", args.gold_bucket),
                               ("--athena-output", args.athena_output)) if not v]
    if missing:
        p.error(f"다음 값이 필요합니다(인자 또는 환경변수로 지정): {', '.join(missing)}")
    return args


def run_query(athena, database, output_location, sql, timeout_sec=120):
    resp = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": output_location},
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


def fetch_all_rows(athena, qid):
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
    """Athena get_query_results는 전부 문자열로 내려주므로, JS/build_dashboard_data.py가
    기대하는 숫자/불리언 타입으로 되돌린다."""
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


def save(name, data):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  {name}: {len(data)}건 -> {path}")


def main():
    args = get_args()
    print(f"Athena: database={args.database} region={args.region}")
    athena = boto3.client("athena", region_name=args.region)

    print("Silver (video_analysis, 카테고리별):")
    for cat_key, cat_id in CATEGORIES.items():
        sql = VIDEO_ANALYSIS_CTE.format(db=args.database) + f"""
SELECT video_id, channel_id, category_id, title, duration_seconds, video_type,
       view_count, like_count, comment_count,
       published_day_of_week, published_hour_kst,
       views_per_day, like_rate, comment_rate,
       subscriber_segment, duration_bucket, upload_time_bucket
FROM video_analysis
WHERE category_id = '{cat_id}'
"""
        qid = run_query(athena, args.database, args.athena_output, sql)
        rows = fetch_all_rows(athena, qid)
        rows = coerce_types(
            rows,
            int_fields=("duration_seconds", "view_count", "like_count", "comment_count",
                        "published_day_of_week", "published_hour_kst"),
            float_fields=("views_per_day", "like_rate", "comment_rate"),
        )
        save(f"video_analysis_{cat_key}.json", rows)

    print("Silver (dim_channel, 채널별 최신값):")
    sql = f"""
SELECT channel_id, channel_name, subscriber_count, channel_thumbnail_url,
       channel_total_view_count, channel_total_video_count, channel_published_at_utc
FROM (
    SELECT channel_id, channel_name, subscriber_count, channel_thumbnail_url,
           channel_total_view_count, channel_total_video_count, channel_published_at_utc,
           ROW_NUMBER() OVER (PARTITION BY channel_id ORDER BY collected_at_utc DESC) AS rn
    FROM {args.database}.silver_youtube
    WHERE is_valid = true
)
WHERE rn = 1
"""
    qid = run_query(athena, args.database, args.athena_output, sql)
    rows = fetch_all_rows(athena, qid)
    rows = coerce_types(
        rows,
        int_fields=("subscriber_count", "channel_total_view_count", "channel_total_video_count"),
    )
    save("dim_channel.json", rows)

    print("Gold (analysis_week 버저닝 테이블, 전체 주차):")
    gold_int_fields = {
        "gold_category_benchmark": ("sample_video_count", "sample_channel_count"),
        "gold_upload_strategy": ("published_day_of_week", "sample_video_count", "strategy_rank"),
        "gold_new_creator_guide": ("recommended_day_of_week", "evidence_video_count"),
    }
    gold_float_fields = {
        "gold_category_benchmark": ("median_duration_seconds", "median_views_per_day", "median_like_rate"),
        "gold_upload_strategy": ("median_views_per_day", "p75_views_per_day", "median_like_rate", "median_comment_rate"),
        "gold_new_creator_guide": ("evidence_median_views_per_day", "evidence_median_like_rate"),
    }
    gold_bool_fields = {
        "gold_upload_strategy": ("is_recommended",),
    }
    for table in ("gold_category_benchmark", "gold_upload_strategy", "gold_new_creator_guide"):
        sql = f"SELECT * FROM {args.database}.{table} ORDER BY analysis_week DESC"
        qid = run_query(athena, args.database, args.athena_output, sql)
        rows = fetch_all_rows(athena, qid)
        rows = coerce_types(
            rows,
            int_fields=gold_int_fields.get(table, ()),
            float_fields=gold_float_fields.get(table, ()),
            bool_fields=gold_bool_fields.get(table, ()),
        )
        save(f"{table}.json", rows)

    print("DONE")


if __name__ == "__main__":
    main()
