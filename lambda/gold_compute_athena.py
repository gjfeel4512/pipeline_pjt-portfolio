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

2026-09-03: 이 세 값은 예전엔 모듈 로드 시점에 os.environ[...]로 필수 검증했는데,
frontend/scripts/export_athena_for_dashboard.py가 이 모듈에서 VIDEO_ANALYSIS_CTE/
CATEGORY_NAME_KO만 재사용하려고 import만 해도(자기 로직은 --database 등 CLI 인자로
따로 받음) 환경변수가 없으면 import 시점에 KeyError로 죽는 문제가 있었다. 그래서
모듈 로드 시점엔 os.environ.get(...)으로만 읽고, 실제로 Lambda가 이 값들을 쓰는
lambda_handler() 안에서 검증하도록 바꿨다 - 실제 Lambda 실행 환경(infra/gold_athena.tf가
항상 세 값을 설정함)에서는 동작이 동일하다.
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

ATHENA_DATABASE = os.environ.get("ATHENA_DATABASE")
ATHENA_OUTPUT_LOCATION = os.environ.get("ATHENA_OUTPUT_LOCATION")
GOLD_BUCKET_NAME = os.environ.get("GOLD_BUCKET_NAME")
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
WITH video_analysis_raw AS (
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
        -- sql/youtube_pipeline_schema_postgresql.sql의 vw_video_analysis.video_age_days와
        -- 동일 정의(수집 시각 - 게시 시각, 최소 1일). frontend/scripts/build_dashboard_data.py의
        -- normalize_silver_row()가 이 값이 없으면 그 행을 통째로 버리므로(video_age_days is
        -- None -> return None) 반드시 SELECT 목록에 있어야 한다.
        CAST(GREATEST(1, FLOOR(
            (to_unixtime(from_iso8601_timestamp(collected_at_utc))
             - to_unixtime(from_iso8601_timestamp(published_at_utc))) / 86400.0
        )) AS INTEGER) AS video_age_days,
        ROUND(
            CAST(view_count AS DOUBLE) / GREATEST(1, FLOOR(
                (to_unixtime(from_iso8601_timestamp(collected_at_utc))
                 - to_unixtime(from_iso8601_timestamp(published_at_utc))) / 86400.0
            )), 4
        ) AS views_per_day,
        ROUND(CAST(like_count AS DOUBLE) / NULLIF(view_count, 0), 6) AS like_rate,
        ROUND(CAST(comment_count AS DOUBLE) / NULLIF(view_count, 0), 6) AS comment_rate,
        -- vw_video_analysis.subscriber_count_at_collection과 동일 - dim_channel 조인이
        -- 실패한 채널에 대한 폴백으로 build_dashboard_data.py의 normalize_silver_row()가 씀.
        subscriber_count AS subscriber_count_at_collection,
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
        END AS upload_time_bucket,
        -- 2026-09-09 버그 수정(사용자 리포트: "지난 기간 대비" 증감률이 비정상적으로
        -- 큼): silver_youtube는 영상 1건 x 수집일 1건의 스냅샷을 그대로 계속 쌓아두는
        -- 시계열 테이블이라(sql/youtube_pipeline_schema_postgresql.sql의
        -- fact_video_snapshot 설계 그대로) 같은 영상이 추적 기간 동안 여러 스냅샷으로
        -- 중복으로 잡힌다. frontend/scripts/build_dashboard_data.py의
        -- load_silver_videos()는 이미 "video_id별로 collected_at_utc가 가장 늦은
        -- 스냅샷 1개만 남긴다"는 동일한 처리를 하고 있는데(그 이유: "남기지 않으면
        -- 옛 스냅샷이 랭킹에 섞여 조회수가 갱신 안 된 것처럼 보인다"), 정작 이 Gold
        -- 집계 SQL에는 그 처리가 빠져 있었다. 그 결과 median_views_per_day 등이 매주
        -- 계속 쌓이는 중복 스냅샷 구성에 따라 흔들리고, "지난 기간 대비"가 실제
        -- 트렌드가 아니라 이 흔들림을 보여주고 있었다. 아래 video_analysis(필터링
        -- 단계)에서 video_id별 최신 스냅샷 1개만 남겨서, load_silver_videos()와
        -- 동일한 "현재 상태 스냅샷"을 이 SQL도 보게 한다.
        ROW_NUMBER() OVER (
            PARTITION BY video_id
            ORDER BY collected_at_utc DESC
        ) AS rn
    FROM {db}.silver_youtube
    WHERE is_valid = true
      AND video_type IN ('short', 'medium', 'long')
      AND published_at_kst IS NOT NULL AND published_at_kst <> ''
      AND published_at_utc IS NOT NULL AND published_at_utc <> ''
      AND collected_at_utc IS NOT NULL AND collected_at_utc <> ''
),
video_analysis AS (
    SELECT
        video_id, channel_id, category_id, title, duration_seconds, video_type,
        view_count, like_count, comment_count,
        published_day_of_week, published_hour_kst, video_age_days, views_per_day,
        like_rate, comment_rate, subscriber_count_at_collection, subscriber_segment,
        duration_bucket, upload_time_bucket
    FROM video_analysis_raw
    WHERE rn = 1
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
        CAST(COUNT(DISTINCT video_id) AS INTEGER) AS sample_video_count,
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
        CAST(COUNT(DISTINCT video_id) AS INTEGER) AS sample_video_count,
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

# 2026-09-03 버그 수정: gold_upload_strategy.strategy_rank는 (category_id, subscriber_segment)
# 안의 "모든" 조합(표본 1~2건짜리 극단치 포함, GROUP BY가 6개 차원이라 조합이 300개 이상)을
# median_views_per_day로만 랭킹한 값이라 is_recommended(sample_video_count >= 30)를 전혀
# 고려하지 않는다. 그래서 "strategy_rank = 1 AND is_recommended = true"를 그대로 AND로 걸면
# 거의 항상 공집합이 됨 - 실측 데이터로 확인해보면 new/early_growth 세그먼트의 rank=1 조합은
# 표본 2~8건짜리뿐이라 전부 is_recommended=false, 그 결과 gold_new_creator_guide가 매주
# 0건으로 쌓여왔다(S3에 0바이트 guide.jsonl만 생성). is_recommended=true인 행들만 먼저
# 걸러낸 뒤 그 안에서 다시 랭킹해야 실제로 표본이 충분한 "1등 조합"이 뽑힌다.
CREATOR_GUIDE_CANDIDATES_SQL = """
WITH qualified AS (
    SELECT category_id, subscriber_segment, video_type, duration_bucket,
           published_day_of_week, upload_time_bucket, sample_video_count,
           median_views_per_day, median_like_rate,
           ROW_NUMBER() OVER (
               PARTITION BY category_id, subscriber_segment
               ORDER BY median_views_per_day DESC NULLS LAST
           ) AS qualified_rank
    FROM {db}.gold_upload_strategy
    WHERE analysis_week = '{week}'
      AND subscriber_segment IN ('new', 'early_growth')
      AND is_recommended = true
)
SELECT category_id, subscriber_segment, video_type, duration_bucket,
       published_day_of_week, upload_time_bucket, sample_video_count,
       median_views_per_day, median_like_rate
FROM qualified
WHERE qualified_rank = 1
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


athena_bytes_scanned = 0  # 이번 실행에서 run_query() 누적 호출로 스캔한 바이트 총합


def run_query(sql, timeout_sec=120):
    global athena_bytes_scanned
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
            # 쿼리 비용(스캔 바이트) 추세를 보려는 것 - 알람은 "성공/실패"만 보므로
            # 쿼리가 갈수록 더 많은 데이터를 훑게 되는 건 알람에 안 잡힌다.
            athena_bytes_scanned += r["QueryExecution"].get("Statistics", {}).get("DataScannedInBytes", 0)
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
    missing = [
        name
        for name, value in (
            ("ATHENA_DATABASE", ATHENA_DATABASE),
            ("ATHENA_OUTPUT_LOCATION", ATHENA_OUTPUT_LOCATION),
            ("GOLD_BUCKET_NAME", GOLD_BUCKET_NAME),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"다음 환경변수가 필요합니다: {', '.join(missing)}")

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

    result["athena_bytes_scanned"] = athena_bytes_scanned

    logger.info("=" * 70)
    logger.info("Gold 집계 완료: %s", json.dumps(result, ensure_ascii=False))
    logger.info("=" * 70)
    emit_run_metrics(athena_bytes_scanned, len(guide_records))
    return result


def emit_run_metrics(bytes_scanned, guide_count):
    """CloudWatch 커스텀 메트릭(수치) - 실패 여부만 보는 기존 알람에는 안 잡히는 두 가지:
    쿼리 비용 추세(AthenaDataScannedBytes)와 이번 주 실제로 생성된 gold_new_creator_guide
    건수(과거에 랭킹 필터 버그로 매주 0건이 나온 적이 있었음 - 성공했지만 사실상 빈
    결과였던 경우, 실행 자체는 안 실패해서 기존 알람이 못 잡았다). 메트릭 전송 실패가
    본 집계를 실패로 만들면 안 되므로 예외를 삼킨다."""
    try:
        boto3.client("cloudwatch", region_name=AWS_REGION).put_metric_data(
            Namespace="Pipeline/PJT",
            MetricData=[
                {"MetricName": "AthenaDataScannedBytes", "Value": float(bytes_scanned), "Unit": "Bytes"},
                {"MetricName": "GoldNewCreatorGuideCount", "Value": float(guide_count), "Unit": "Count"},
            ],
        )
    except Exception as e:  # noqa: BLE001 - 관측용 부가 기능, 본 실행을 절대 막지 않음
        logger.warning("CloudWatch 메트릭 전송 실패(무시하고 계속): %s", e)
