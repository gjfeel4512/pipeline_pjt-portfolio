#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lambda/gold_compute_athena.py
------------------------------
sql/compute_gold.sql(PostgreSQL, Airflow silver_to_gold_dag.py)을 대체하는
서버리스 Gold 집계 Lambda. PostgreSQL/RDS 없이 Athena(Glue Catalog의
silver_youtube 테이블)만으로 Gold 3종(gold_category_benchmark,
gold_upload_strategy, gold_new_creator_guide)을 계산해서 S3 Gold 버킷에
직접 쓴다 (Athena INSERT INTO의 결과가 곧 최종 위치라 별도 export 단계가 없음).

gold_video_rank_trend는 이식하지 않음 - 유일한 데이터 소스였던
trending_rank_tracker.py Lambda가 삭제되어(2026-09-03) 더 이상 trending_rank가
채워지지 않으므로 대상 자체가 없음.

멱등성: analysis_week(이번 주 월요일, KST)마다 기존 S3 파티션 객체를 먼저
지우고(purge) 다시 쓴다 - Postgres의 ON CONFLICT ... DO UPDATE와 동등한 효과를
"주 단위 파티션 통째로 덮어쓰기"로 구현.

환경변수:
  ATHENA_DATABASE        Glue 데이터베이스명 (예: goldline_dev_db)
  ATHENA_OUTPUT_LOCATION Athena 쿼리 결과/메타데이터 저장용 S3 경로
  GOLD_BUCKET_NAME       Gold S3 버킷 이름
"""
import datetime
import json
import logging
import os
import time
import uuid

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ATHENA_DATABASE = os.environ["ATHENA_DATABASE"]
ATHENA_OUTPUT_LOCATION = os.environ["ATHENA_OUTPUT_LOCATION"]
GOLD_BUCKET_NAME = os.environ["GOLD_BUCKET_NAME"]
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "primary")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")

MIN_SAMPLE_COUNT = 30
KST = datetime.timezone(datetime.timedelta(hours=9))

CATEGORY_NAME_KO = {
    "1": "영화·애니메이션",
    "2": "자동차·차량",
    "20": "게임",
    "22": "인물·블로그",
}
DAY_KO = {1: "월", 2: "화", 3: "수", 4: "목", 5: "금", 6: "토", 7: "일"}

athena = boto3.client("athena", region_name=AWS_REGION)
s3 = boto3.client("s3", region_name=AWS_REGION)

# ----------------------------------------------------------------------------
# Silver -> vw_video_analysis(Postgres 뷰)와 동등한 Athena/Presto CTE.
# sql/youtube_pipeline_schema_postgresql.sql의 vw_video_analysis 정의와
# transforms/load_silver_to_postgres.py의 prepare_row()를 그대로 옮긴 것 -
# 두 함수 모두 바뀌면 이 CTE도 같이 바꿔야 한다.
# ----------------------------------------------------------------------------
VIDEO_ANALYSIS_CTE = """
WITH video_analysis AS (
    SELECT
        video_id, channel_id, CAST(category_id AS VARCHAR) AS category_id, title,
        duration_seconds,
        CASE video_type
            WHEN 'short' THEN 'shorts'
            WHEN 'medium' THEN 'short_form'
            WHEN 'long' THEN 'long_form'
        END AS video_type,
        view_count, like_count, comment_count,
        CAST(day_of_week(from_iso8601_timestamp(published_at_kst)) AS INTEGER) AS published_day_of_week,
        hour(from_iso8601_timestamp(published_at_kst)) AS published_hour_kst,
        ROUND(
            CAST(view_count AS DOUBLE) / GREATEST(1, FLOOR(
                (to_unixtime(from_iso8601_timestamp(collected_at_utc))
                 - to_unixtime(from_iso8601_timestamp(published_at_utc))) / 86400.0
            )), 4
        ) AS views_per_day,
        ROUND(CAST(like_count AS DOUBLE) / NULLIF(view_count, 0), 6) AS like_rate,
        ROUND(CAST(comment_count AS DOUBLE) / NULLIF(view_count, 0), 6) AS comment_rate,
        CASE
            WHEN subscriber_count IS NULL THEN 'hidden_or_unknown'
            WHEN subscriber_count < 1000 THEN 'new'
            WHEN subscriber_count < 10000 THEN 'early_growth'
            WHEN subscriber_count < 100000 THEN 'growth'
            ELSE 'established'
        END AS subscriber_segment,
        CASE
            WHEN duration_seconds <= 60 THEN 'shorts'
            WHEN duration_seconds <= 300 THEN '1_to_5m'
            WHEN duration_seconds <= 600 THEN '5_to_10m'
            WHEN duration_seconds <= 1200 THEN '10_to_20m'
            ELSE '20m_plus'
        END AS duration_bucket,
        CASE
            WHEN hour(from_iso8601_timestamp(published_at_kst)) BETWEEN 0 AND 5 THEN 'dawn'
            WHEN hour(from_iso8601_timestamp(published_at_kst)) BETWEEN 6 AND 11 THEN 'morning'
            WHEN hour(from_iso8601_timestamp(published_at_kst)) BETWEEN 12 AND 17 THEN 'afternoon'
            WHEN hour(from_iso8601_timestamp(published_at_kst)) BETWEEN 18 AND 21 THEN 'evening'
            ELSE 'night'
        END AS upload_time_bucket
    FROM {db}.silver_youtube
    WHERE is_valid = true
      AND video_type IN ('short', 'medium', 'long')
      AND published_at_kst IS NOT NULL AND published_at_kst <> ''
      AND published_at_utc IS NOT NULL AND published_at_utc <> ''
      AND collected_at_utc IS NOT NULL AND collected_at_utc <> ''
)
"""

CATEGORY_BENCHMARK_SQL = "INSERT INTO {db}.gold_category_benchmark\n" + VIDEO_ANALYSIS_CTE + """
, by_time AS (
    SELECT category_id, upload_time_bucket, approx_percentile(views_per_day, 0.5) AS med_views
    FROM video_analysis GROUP BY category_id, upload_time_bucket
),
best_time AS (
    SELECT category_id, upload_time_bucket FROM (
        SELECT category_id, upload_time_bucket,
               ROW_NUMBER() OVER (PARTITION BY category_id ORDER BY med_views DESC NULLS LAST) AS rn
        FROM by_time
    ) WHERE rn = 1
),
by_duration AS (
    SELECT category_id, duration_bucket, approx_percentile(views_per_day, 0.5) AS med_views
    FROM video_analysis GROUP BY category_id, duration_bucket
),
best_duration AS (
    SELECT category_id, duration_bucket FROM (
        SELECT category_id, duration_bucket,
               ROW_NUMBER() OVER (PARTITION BY category_id ORDER BY med_views DESC NULLS LAST) AS rn
        FROM by_duration
    ) WHERE rn = 1
),
by_type AS (
    SELECT category_id, video_type, approx_percentile(views_per_day, 0.5) AS med_views
    FROM video_analysis GROUP BY category_id, video_type
),
best_type AS (
    SELECT category_id, video_type FROM (
        SELECT category_id, video_type,
               ROW_NUMBER() OVER (PARTITION BY category_id ORDER BY med_views DESC NULLS LAST) AS rn
        FROM by_type
    ) WHERE rn = 1
),
agg AS (
    SELECT
        category_id,
        CAST(COUNT(*) AS INTEGER) AS sample_video_count,
        CAST(COUNT(DISTINCT channel_id) AS INTEGER) AS sample_channel_count,
        approx_percentile(CAST(duration_seconds AS DOUBLE), 0.5) AS median_duration_seconds,
        approx_percentile(views_per_day, 0.5) AS median_views_per_day,
        approx_percentile(like_rate, 0.5) AS median_like_rate
    FROM video_analysis
    GROUP BY category_id
)
SELECT
    agg.category_id, agg.sample_video_count, agg.sample_channel_count,
    agg.median_duration_seconds, agg.median_views_per_day, agg.median_like_rate,
    best_time.upload_time_bucket, best_duration.duration_bucket, best_type.video_type,
    '{created_at}' AS created_at_utc,
    '{week}' AS analysis_week
FROM agg
LEFT JOIN best_time ON best_time.category_id = agg.category_id
LEFT JOIN best_duration ON best_duration.category_id = agg.category_id
LEFT JOIN best_type ON best_type.category_id = agg.category_id
"""

UPLOAD_STRATEGY_SQL = "INSERT INTO {db}.gold_upload_strategy\n" + VIDEO_ANALYSIS_CTE + """
, agg AS (
    SELECT
        category_id, subscriber_segment, video_type, duration_bucket,
        published_day_of_week, upload_time_bucket,
        CAST(COUNT(*) AS INTEGER) AS sample_video_count,
        approx_percentile(views_per_day, 0.5) AS median_views_per_day,
        approx_percentile(views_per_day, 0.75) AS p75_views_per_day,
        approx_percentile(like_rate, 0.5) AS median_like_rate,
        approx_percentile(comment_rate, 0.5) AS median_comment_rate
    FROM video_analysis
    GROUP BY category_id, subscriber_segment, video_type, duration_bucket,
             published_day_of_week, upload_time_bucket
),
ranked AS (
    SELECT *,
        CAST(RANK() OVER (
            PARTITION BY category_id, subscriber_segment
            ORDER BY median_views_per_day DESC NULLS LAST
        ) AS INTEGER) AS strategy_rank
    FROM agg
)
SELECT
    category_id, subscriber_segment, video_type, duration_bucket,
    published_day_of_week, upload_time_bucket, sample_video_count,
    median_views_per_day, p75_views_per_day, median_like_rate, median_comment_rate,
    CAST(NULL AS DOUBLE) AS median_views_per_subscriber,
    strategy_rank,
    (sample_video_count >= {min_sample}) AS is_recommended,
    '{created_at}' AS created_at_utc,
    '{week}' AS analysis_week
FROM ranked
"""

CREATOR_GUIDE_CANDIDATES_SQL = """
SELECT category_id, subscriber_segment, video_type, duration_bucket,
       published_day_of_week, upload_time_bucket, sample_video_count,
       median_views_per_day, median_like_rate
FROM {db}.gold_upload_strategy
WHERE analysis_week = '{week}'
  AND subscriber_segment IN ('new', 'early_growth')
  AND is_recommended = true
  AND strategy_rank = 1
"""


def monday_of_week_kst():
    today_kst = datetime.datetime.now(KST).date()
    return today_kst - datetime.timedelta(days=today_kst.weekday())


def purge_partition(prefix):
    """멱등 재실행을 위해 이번 analysis_week 파티션의 기존 객체를 먼저 지운다."""
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=GOLD_BUCKET_NAME, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        if batch:
            s3.delete_objects(
                Bucket=GOLD_BUCKET_NAME,
                Delete={"Objects": [{"Key": k} for k in batch]},
            )
    logger.info("purge %s: %d개 객체 삭제", prefix, len(keys))
    return len(keys)


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
            raise RuntimeError(f"Athena query {state}: {reason}\nSQL(앞 500자): {sql[:500]}")
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


def build_guide_records(candidates, week, created_at):
    records = []
    for row in candidates:
        category_id = row["category_id"]
        segment = row["subscriber_segment"]
        video_type = row["video_type"]
        duration_bucket = row["duration_bucket"]
        dow = int(row["published_day_of_week"])
        time_bucket = row["upload_time_bucket"]
        sample_count = int(row["sample_video_count"])
        median_views = float(row["median_views_per_day"])
        median_like_rate = float(row["median_like_rate"])
        category_name_ko = CATEGORY_NAME_KO.get(category_id, category_id)

        guide_message = (
            f"{category_name_ko} 카테고리의 {segment} 채널은 {video_type} · {duration_bucket} 길이 · "
            f"{DAY_KO.get(dow, dow)}요일 {time_bucket} 시간대 조합에서 표본 {sample_count}건 기준 "
            f"중앙 일평균 조회수({round(median_views, 1)})와 좋아요율({round(median_like_rate, 6)})이 "
            f"상대적으로 높게 나타났습니다."
        )
        records.append(
            {
                "guide_id": str(uuid.uuid4()),
                "category_id": category_id,
                "target_creator_segment": segment,
                "recommended_video_type": video_type,
                "recommended_duration_bucket": duration_bucket,
                "recommended_day_of_week": dow,
                "recommended_time_bucket": time_bucket,
                "evidence_video_count": sample_count,
                "evidence_median_views_per_day": median_views,
                "evidence_median_like_rate": median_like_rate,
                "guide_message": guide_message,
                "caveat": "이 결과는 수집 표본에 기반한 상관관계이며, 업로드 조건만 바꾼다고 성과가 보장되는 인과관계가 아닙니다.",
                "created_at_utc": created_at,
            }
        )
    return records


def write_guide_to_s3(records, week):
    key = f"gold_new_creator_guide/analysis_week={week}/guide.jsonl"
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    if records:
        body += "\n"
    s3.put_object(Bucket=GOLD_BUCKET_NAME, Key=key, Body=body.encode("utf-8"))
    logger.info("gold_new_creator_guide: %d건 -> s3://%s/%s", len(records), GOLD_BUCKET_NAME, key)


def lambda_handler(event, context):
    week = monday_of_week_kst().isoformat()
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    logger.info("=" * 70)
    logger.info("Gold 집계 시작 - analysis_week=%s", week)
    logger.info("=" * 70)

    result = {"analysis_week": week, "purged": {}, "queries": {}}

    # 1) 이번 주 파티션 purge (재실행/스케줄 반복 실행 시 중복 방지 - Postgres의
    #    ON CONFLICT DO UPDATE와 동등한 효과를 파티션 단위로 구현)
    for table in ("gold_category_benchmark", "gold_upload_strategy", "gold_new_creator_guide"):
        prefix = f"{table}/analysis_week={week}/"
        result["purged"][table] = purge_partition(prefix)

    # 2) gold_category_benchmark
    sql1 = CATEGORY_BENCHMARK_SQL.format(db=ATHENA_DATABASE, created_at=created_at, week=week)
    qid1 = run_query(sql1)
    result["queries"]["gold_category_benchmark"] = qid1
    logger.info("gold_category_benchmark 완료 (QueryExecutionId=%s)", qid1)

    # 3) gold_upload_strategy
    sql2 = UPLOAD_STRATEGY_SQL.format(
        db=ATHENA_DATABASE, created_at=created_at, week=week, min_sample=MIN_SAMPLE_COUNT
    )
    qid2 = run_query(sql2)
    result["queries"]["gold_upload_strategy"] = qid2
    logger.info("gold_upload_strategy 완료 (QueryExecutionId=%s)", qid2)

    # 4) gold_new_creator_guide - gold_upload_strategy 결과에서 top1만 뽑아
    #    Python에서 guide_message를 만들어 S3에 직접 씀 (Presto format()에
    #    의존하지 않기 위해 SQL이 아니라 Lambda 안에서 문자열을 조립한다)
    sql3 = CREATOR_GUIDE_CANDIDATES_SQL.format(db=ATHENA_DATABASE, week=week)
    qid3 = run_query(sql3)
    candidates = fetch_all_rows(qid3)
    guide_records = build_guide_records(candidates, week, created_at)
    write_guide_to_s3(guide_records, week)
    result["queries"]["gold_new_creator_guide_candidates"] = qid3
    result["gold_new_creator_guide_count"] = len(guide_records)

    logger.info("=" * 70)
    logger.info("Gold 집계 완료: %s", json.dumps(result, ensure_ascii=False))
    logger.info("=" * 70)
    return result
