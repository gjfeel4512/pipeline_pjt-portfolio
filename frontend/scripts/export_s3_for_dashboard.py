#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
frontend/scripts/export_s3_for_dashboard.py
---------------------------------------------
로컬(또는 팀 RDS) PostgreSQL을 거치지 않고, AWS S3에 있는 Silver/Gold 데이터를
직접 읽어서 outputs/silver_gold_export/*.json 을 만든다.

build_dashboard_data.py는 outputs/silver_gold_export/ 안의 파일만 보고 동작하므로,
이 스크립트가 만드는 결과물의 "모양"(파일명/필드명)만 export_pg_for_dashboard.py와
똑같이 맞추면 build_dashboard_data.py는 단 한 줄도 고칠 필요가 없다.

지금까지의 경로:
  S3 Silver --(load_silver_to_postgres.py)--> PostgreSQL
           --(compute_gold.sql)--> PostgreSQL Gold
           --(export_pg_for_dashboard.py)--> outputs/silver_gold_export/*.json

이 스크립트가 대신하는 경로 (PostgreSQL 불필요):
  S3 Silver(JSONL, 직접 읽음) + Gold(analysis_week 버저닝 3개 테이블은 Athena로 조회,
  gold_video_rank_trend는 Glue 카탈로그에 없어서 S3 직접 읽음 - 아래 "Gold를 왜 Athena로
  읽나" 참고) --(이 스크립트)--> outputs/silver_gold_export/*.json

Gold를 왜 Athena로 읽나 (2026-09, 사용자 요청으로 전환):
  Gold 3개 테이블(gold_category_benchmark/gold_upload_strategy/gold_new_creator_guide)은
  infra/glue.tf에 Glue Catalog 테이블로 이미 등록돼 있고(파티션 프로젝션 포함, PR #11/#12),
  이미 완성된 집계 결과라 Silver처럼 Python으로 재계산할 파생 로직이 없다 - 그냥
  `SELECT * FROM {table}`이면 충분해서 S3 키를 직접 파싱하며 파티션값을 복원하는 것보다
  Athena/Glue 카탈로그를 그대로 쓰는 쪽이 더 안전하다(파티션 파싱 버그 걱정 없음, 스키마는
  Glue 카탈로그가 단일 진실 공급원). gold_video_rank_trend는 트렌딩 순위 추적 기능이
  제거되면서(ARCHITECTURE_NOTE.md) Glue 테이블이 안 만들어져 있어서 기존 방식(S3 직접
  읽기, iter_s3_gold_json)을 그대로 쓴다.
  Silver는 그대로 S3 직접 읽기 유지 - Athena로 옮기려면 vw_video_analysis의 파생 계산
  전체를 Athena SQL로 다시 써야 하는 큰 재작업이라 이번엔 범위에서 뺌(사용자 확인:
  "Gold 쓰고 필요하면 Silver까지"라고 했지만 Gold만으로 충분하다고 판단).

Silver 파생 계산(video_age_days/views_per_day/like_rate/duration_bucket/
upload_time_bucket/subscriber_segment 등)은 원래 PostgreSQL이 두 군데서 나눠서 하던 일이다:
  1) transforms/load_silver_to_postgres.py의 prepare_row()
     - Silver JSONL 원본 -> fact_video_snapshot 행 (타입 매핑, KST 시각 분해 등)
  2) sql/youtube_pipeline_schema_postgresql.sql의 vw_video_analysis 뷰
     - fact_video_snapshot -> video_age_days/views_per_day/like_rate/... 파생 지표 계산
     - WHERE is_public = TRUE AND is_valid = TRUE
이 스크립트의 transform_record()/build_video_row()가 그 두 단계를 Python으로 그대로
옮긴 것이다. **수식이 SQL과 정확히 같아야 하므로, 두 원본 파일이 바뀌면 이 스크립트도
반드시 같이 맞춰줘야 한다.**

dim_channel도 마찬가지로, load_silver_to_postgres.py의 load()가 "Silver 레코드마다
(is_public 여부와 무관하게) dim_channel을 upsert"하던 것을 그대로 재현한다 - 채널
가장 최근 수집 레코드(collected_at_utc 기준)의 채널 필드를 쓴다.

Gold 파일 형식(.json 배열 vs .jsonl): 2026-09 Athena/Glue 연동 커밋 이후
export_gold_to_s3.py가 JSON 배열(.json) 대신 JSON Lines(.jsonl)로 저장하고,
analysis_week/exported_at 값도 레코드 안이 아니라 S3 파티션 키에만 남기도록 바뀌었다.
iter_s3_gold_json()이 신/구 포맷을 모두 읽고 파티션 값을 다시 채워 넣으므로 이
스크립트는 그대로 써도 되지만, export_gold_to_s3.py의 저장 형식이 또 바뀌면 그
함수도 같이 봐야 한다.

사전 준비
--------
  1) AWS 자격증명이 필요하다 (이 스크립트를 실행하는 컴퓨터/환경에 아래 중 하나로 설정):
       - `aws configure`로 등록해둔 로컬 프로필
       - 환경변수 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (임시자격증명이면 AWS_SESSION_TOKEN도)
       - CI(GitHub Actions 등) 환경이면 그 환경의 Secrets로 주입된 자격증명
     아래 권한이 있어야 한다 (버킷명은 infra/outputs.tf의 terraform output,
     docker-compose-aws.yml과 동일):
       - Silver 버킷(goldline-dev-silver-827913617635, 환경변수 AWS_S3_SILVER_BUCKET로
         재정의 가능): s3:ListBucket + s3:GetObject
       - Gold 버킷(goldline-dev-gold-827913617635, 환경변수 AWS_S3_GOLD_BUCKET로 재정의
         가능): s3:ListBucket + s3:GetObject (gold_video_rank_trend 직접 읽기용) +
         s3:PutObject (Athena 쿼리 결과 임시 저장 위치, 기본
         s3://<gold-bucket>/athena-query-results/ - 환경변수 AWS_ATHENA_OUTPUT_LOCATION로
         재정의 가능)
       - Athena/Glue: athena:StartQueryExecution, athena:GetQueryExecution,
         athena:GetQueryResults, glue:GetTable, glue:GetDatabase, glue:GetPartitions
         (Gold 3개 버저닝 테이블을 Athena로 SELECT * 하기 위함 - 환경변수
         AWS_ATHENA_DATABASE로 재정의 가능, 기본 goldline_dev_db)
  2) pip install boto3   (requirements.txt에 이미 포함되어 있음)

사용
----
  python frontend/scripts/export_s3_for_dashboard.py
  python frontend/scripts/export_s3_for_dashboard.py \
      --region us-west-2 \
      --silver-bucket goldline-dev-silver-827913617635 \
      --gold-bucket goldline-dev-gold-827913617635

  # AWS 접속 없이 계산 로직만 테스트하고 싶을 때 (팀원 로컬 개발/디버깅용):
  #   --local-silver-path: outputs/silver/{category}.jsonl 형태의 폴더
  #   --local-gold-path:   gold_category_benchmark.json 등이 들어있는 폴더(선택, 없어도 됨)
  python frontend/scripts/export_s3_for_dashboard.py \
      --local-silver-path outputs/silver --local-gold-path outputs/gold_json

이후 (기존과 동일, 수정 없음):
  python frontend/scripts/build_dashboard_data.py
"""
import argparse
import glob
import json
import os
import re
import time
from datetime import date, datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.dirname(SCRIPT_DIR)
ROOT = os.path.dirname(FRONTEND_DIR)
OUT_DIR = os.path.join(ROOT, "outputs", "silver_gold_export")

# lambda/youtube_api_daily.py, dags/*.py 와 동일한 카테고리 기준(22=오염 카테고리는 제외)
CATEGORY_SLUGS = {"gaming": "20", "autos_vehicles": "2", "film_animation": "1"}

DEFAULT_SILVER_BUCKET = "goldline-dev-silver-827913617635"
DEFAULT_GOLD_BUCKET = "goldline-dev-gold-827913617635"
DEFAULT_REGION = "us-west-2"
# infra/glue.tf: aws_glue_catalog_database.pipeline.name = "${replace(resource_prefix, "-", "_")}_db"
DEFAULT_ATHENA_DATABASE = "goldline_dev_db"
DEFAULT_ATHENA_OUTPUT_PREFIX = "athena-query-results/"
ATHENA_POLL_INTERVAL_SEC = 2
ATHENA_POLL_TIMEOUT_SEC = 120

# infra/glue.tf의 각 Gold 테이블 columns 블록과 동일 - Athena get_query_results가
# 모든 값을 문자열(VarCharValue)로 돌려주므로, 원래 타입(int/double/boolean)으로
# 되돌리기 위한 컬럼 목록. 여기 없는 컬럼은 문자열 그대로 둔다.
GOLD_ATHENA_INT_COLUMNS = {
    "gold_category_benchmark": {"sample_video_count", "sample_channel_count"},
    "gold_upload_strategy": {"published_day_of_week", "sample_video_count", "strategy_rank"},
    "gold_new_creator_guide": {"recommended_day_of_week", "evidence_video_count"},
}
GOLD_ATHENA_FLOAT_COLUMNS = {
    "gold_category_benchmark": {"median_duration_seconds", "median_views_per_day", "median_like_rate"},
    "gold_upload_strategy": {
        "median_views_per_day", "p75_views_per_day", "median_like_rate",
        "median_comment_rate", "median_views_per_subscriber",
    },
    "gold_new_creator_guide": {"evidence_median_views_per_day", "evidence_median_like_rate"},
}
GOLD_ATHENA_BOOL_COLUMNS = {
    "gold_upload_strategy": {"is_recommended"},
}

# transforms/load_silver_to_postgres.py 와 동일
VIDEO_TYPE_MAP = {"short": "shorts", "medium": "short_form", "long": "long_form"}
DOW_KO = {1: "월", 2: "화", 3: "수", 4: "목", 5: "금", 6: "토", 7: "일"}

GOLD_VERSIONED_TABLES = ["gold_category_benchmark", "gold_upload_strategy", "gold_new_creator_guide"]


def none_if_empty(value):
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def parse_kst(published_at_kst):
    """'2026-08-30T12:45:56+09:00' -> naive datetime (KST 벽시계 값). load_silver_to_postgres.py와 동일."""
    if not published_at_kst:
        return None
    try:
        dt = datetime.fromisoformat(published_at_kst)
    except ValueError:
        return None
    return dt.replace(tzinfo=None)


def parse_utc(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 원본 읽기 (S3 또는 로컬 테스트 경로)
# ---------------------------------------------------------------------------

def iter_s3_silver_jsonl(s3, bucket, category_slug):
    """s3://{bucket}/youtube/silver/category={slug}/... 아래 .jsonl 전부. rejected(오염)
    경로(youtube/silver-rejected/)는 접두사가 달라 자동으로 제외된다."""
    prefix = f"youtube/silver/category={category_slug}/"
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".jsonl"):
                continue
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
            for line in body.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def iter_local_silver_jsonl(base_path, category_slug):
    """--local-silver-path 테스트용. outputs/silver/{slug}.jsonl (build_silver_from_bronze_local.py
    산출물) 또는 파일명에 slug가 들어간 *.jsonl 아무거나 다 읽는다."""
    patterns = [
        os.path.join(base_path, f"{category_slug}.jsonl"),
        os.path.join(base_path, "**", f"*{category_slug}*.jsonl"),
    ]
    seen = set()
    for pattern in patterns:
        for fp in sorted(glob.glob(pattern, recursive=True)):
            if fp in seen:
                continue
            seen.add(fp)
            with open(fp, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue


def _partition_value_from_key(key, partition_name):
    """S3 키 's3://.../gold_category_benchmark/analysis_week=2026-08-24/...'에서
    'analysis_week=' 뒤의 값을 뽑아낸다."""
    m = re.search(rf"{re.escape(partition_name)}=([^/]+)/", key)
    return m.group(1) if m else None


def iter_s3_gold_json(s3, bucket, table, partition_name):
    """s3://{bucket}/{table}/{partition_name}=.../{table}.json(l) 전부 읽어서
    (partition_key, rows) 리스트로 반환. transforms/export_gold_to_s3.py의 key 규칙과 동일.

    2026-09 Athena/Glue 연동 커밋(infra/glue.tf 추가, "feat: Gold 테이블 3개를 Athena로도
    쿼리 가능하게 만듦")부터 export_gold_to_s3.py의 저장 형식이 두 가지 바뀌었다:
      1) JSON 배열(.json) 대신 JSON Lines(.jsonl, 한 줄 = 레코드 하나) - Athena JSON
         SerDe가 배열을 못 읽어서.
      2) analysis_week/exported_at 값을 레코드 안에서 빼고 S3 파티션 키에만 남김 -
         Athena가 파티션 컬럼이 데이터 파일에도 있으면 그 파일을 조용히 0건 처리해서.
    build_dashboard_data.py는 각 행의 analysis_week 필드로 정렬하므로, 파티션 키에서
    값을 다시 읽어서 채워 넣는다. 신(.jsonl)/구(.json) 포맷을 둘 다 읽을 수 있게 만들어서,
    아직 예전 형식으로 남아있는 과거 파티션이 있어도 깨지지 않는다."""
    out = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{table}/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".jsonl"):
                body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
                rows = []
                for line in body.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            elif key.endswith(".json"):
                body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
                try:
                    rows = json.loads(body)
                except json.JSONDecodeError:
                    continue
            else:
                continue

            partition_value = _partition_value_from_key(key, partition_name)
            if partition_value:
                for row in rows:
                    row.setdefault(partition_name, partition_value)
            out.append((key, rows))
    return out


def _coerce_athena_value(table, column, value):
    if value is None:
        return None
    if column in GOLD_ATHENA_INT_COLUMNS.get(table, ()):
        return int(value)
    if column in GOLD_ATHENA_FLOAT_COLUMNS.get(table, ()):
        return float(value)
    if column in GOLD_ATHENA_BOOL_COLUMNS.get(table, ()):
        return value.lower() == "true"
    return value


def query_athena_gold_table(athena, database, table, output_location):
    """Athena로 `SELECT * FROM {database}.{table}`을 돌려서 list[dict]로 반환한다.
    파티션 컬럼(analysis_week)도 Athena가 SELECT *에 자동으로 포함해주므로, S3 키에서
    파티션값을 직접 파싱하던 iter_s3_gold_json()의 역할을 대신한다."""
    query = f"SELECT * FROM {database}.{table}"
    start = athena.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": output_location},
    )
    query_id = start["QueryExecutionId"]

    waited = 0
    state = "QUEUED"
    reason = ""
    while waited < ATHENA_POLL_TIMEOUT_SEC:
        status = athena.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]
        state = status["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            reason = status.get("StateChangeReason", "")
            break
        time.sleep(ATHENA_POLL_INTERVAL_SEC)
        waited += ATHENA_POLL_INTERVAL_SEC

    if state != "SUCCEEDED":
        raise RuntimeError(f"Athena 쿼리 실패 ({table}): state={state} reason={reason}")

    rows = []
    columns = None
    paginator = athena.get_paginator("get_query_results")
    for page in paginator.paginate(QueryExecutionId=query_id):
        page_rows = page["ResultSet"]["Rows"]
        if columns is None:
            # 첫 페이지의 첫 행이 헤더(컬럼명)다.
            columns = [c.get("VarCharValue") for c in page_rows[0]["Data"]]
            page_rows = page_rows[1:]
        for r in page_rows:
            values = [c.get("VarCharValue") for c in r["Data"]]
            row = dict(zip(columns, values))
            rows.append({col: _coerce_athena_value(table, col, val) for col, val in row.items()})
    return rows


def load_local_gold_json(base_path, table):
    path = os.path.join(base_path, f"{table}.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


# ---------------------------------------------------------------------------
# Silver 레코드 -> fact_video_snapshot(prepare_row) -> vw_video_analysis 파생 계산
# ---------------------------------------------------------------------------

def transform_record(rec):
    """Silver JSONL 레코드 1건 -> 내부 작업용 dict, 또는 스키마 제약을 못 만족하면 None.
    transforms/load_silver_to_postgres.py의 prepare_row()와 조건/필드가 동일하다
    (is_public으로 걸러내지 않는다 - dim_channel은 is_public과 무관하게 채워지므로,
    필터링은 build_video_row() 쪽에서 한다)."""
    if not rec.get("is_valid", False):
        return None

    video_type = VIDEO_TYPE_MAP.get(rec.get("video_type"))
    if video_type is None:
        return None

    published_at_kst_dt = parse_kst(rec.get("published_at_kst"))
    if published_at_kst_dt is None:
        return None

    collected_at_utc_raw = rec.get("collected_at_utc")
    if not collected_at_utc_raw:
        return None
    collected_dt = parse_utc(collected_at_utc_raw)
    if collected_dt is None:
        return None

    category_id = rec.get("category_id")
    if category_id is None:
        return None
    category_id = str(category_id)

    published_at_utc_raw = none_if_empty(rec.get("published_at_utc"))
    published_at_utc_dt = parse_utc(published_at_utc_raw) if published_at_utc_raw else None

    return {
        "video_id": rec.get("video_id"),
        "channel_id": rec.get("channel_id"),
        "category_id": category_id,
        "title": rec.get("title") or "",
        "published_at_utc_raw": published_at_utc_raw,
        "published_at_utc_dt": published_at_utc_dt,
        "published_at_kst_raw": rec.get("published_at_kst"),
        "published_at_kst_dt": published_at_kst_dt,
        "duration_iso8601": rec.get("duration_iso8601") or "",
        "duration_seconds": rec.get("duration_seconds") or 0,
        "video_type": video_type,
        "view_count": rec.get("view_count") or 0,
        "like_count": rec.get("like_count"),
        "comment_count": rec.get("comment_count"),
        "subscriber_count_at_collection": rec.get("subscriber_count"),
        "collected_at_utc_raw": collected_at_utc_raw,
        "collected_at_utc_dt": collected_dt,
        "collected_date": collected_dt.date(),
        "is_public": (rec.get("privacy_status") == "public"),
        # dim_channel 조인용 (channel_name/subscriber_count 등은 채널 레벨 필드라 영상마다 반복됨)
        "channel_name": rec.get("channel_name"),
        "channel_published_at_utc": none_if_empty(rec.get("channel_published_at_utc")),
        "hidden_subscriber_count": bool(rec.get("hidden_subscriber_count", False)),
        "channel_total_view_count": rec.get("channel_total_view_count"),
        "channel_total_video_count": rec.get("channel_total_video_count"),
        "uploads_playlist_id": rec.get("uploads_playlist_id"),
        "channel_thumbnail_url": rec.get("channel_thumbnail_url"),
    }


def dedup_latest(rows, key_fn):
    """(video_id, collected_date)나 channel_id처럼 같은 키를 가진 행이 여러 개면
    collected_at_utc_dt가 가장 최근인 것만 남긴다. PostgreSQL의
    'ON CONFLICT ... DO UPDATE'(마지막에 적재된 값으로 덮어씀)와 같은 결과를 내기 위한
    것으로, 결정적인 결과를 위해 "마지막으로 처리한 행" 대신 "collected_at_utc가 가장
    늦은 행"을 기준으로 삼는다.

    export 원본에는 같은 영상·같은 collected_at_utc인데 채널 보강 유무만 다른 중복
    행이 대량으로 섞여 있다(gaming distinct의 ~95%가 2배 중복). collected_at_utc_dt가
    동점이면 예전엔 '먼저 순회된 행'이 그냥 남아서, 썸네일 없는 쪽이 이기면 그 채널이
    dim_channel/thumb_by_channel에서 통째로 빠졌다. 동점일 때는 channel_thumbnail_url이
    채워진 행을 우선해 남긴다(그 외 순서/동작은 그대로)."""
    best = {}
    for row in rows:
        k = key_fn(row)
        cur = best.get(k)
        if cur is None:
            best[k] = row
            continue
        if row["collected_at_utc_dt"] > cur["collected_at_utc_dt"]:
            best[k] = row
        elif (
            row["collected_at_utc_dt"] == cur["collected_at_utc_dt"]
            and row.get("channel_thumbnail_url")
            and not cur.get("channel_thumbnail_url")
        ):
            best[k] = row
    return list(best.values())


def build_video_row(row):
    """vw_video_analysis 뷰의 SELECT 계산 + WHERE is_public=TRUE 필터.
    sql/youtube_pipeline_schema_postgresql.sql의 vw_video_analysis 정의와 한 줄씩 대응된다."""
    if not row["is_public"]:
        return None

    view_count = row["view_count"]
    like_count = row["like_count"]
    comment_count = row["comment_count"]
    subscriber_count = row["subscriber_count_at_collection"]
    duration_seconds = row["duration_seconds"]

    published_at_utc_dt = row["published_at_utc_dt"]
    collected_dt = row["collected_at_utc_dt"]
    if published_at_utc_dt is not None:
        delta_seconds = (collected_dt - published_at_utc_dt).total_seconds()
        video_age_days = max(1, int(delta_seconds // 86400))
    else:
        # published_at_utc가 없는 경우는 정상 데이터라면 사실상 없어야 하지만(필수 필드),
        # 방어적으로 SQL의 GREATEST(1, ...)와 같은 하한만 적용한다.
        video_age_days = 1

    views_per_day = round(view_count / video_age_days, 4)
    like_rate = round(like_count / view_count, 6) if (view_count and like_count is not None) else None
    comment_rate = round(comment_count / view_count, 6) if (view_count and comment_count is not None) else None
    views_per_subscriber = (
        round(view_count / subscriber_count, 6) if subscriber_count else None
    )

    if subscriber_count is None:
        subscriber_segment = "hidden_or_unknown"
    elif subscriber_count < 1000:
        subscriber_segment = "new"
    elif subscriber_count < 10000:
        subscriber_segment = "early_growth"
    elif subscriber_count < 100000:
        subscriber_segment = "growth"
    else:
        subscriber_segment = "established"

    if duration_seconds <= 60:
        duration_bucket = "shorts"
    elif duration_seconds <= 300:
        duration_bucket = "1_to_5m"
    elif duration_seconds <= 600:
        duration_bucket = "5_to_10m"
    elif duration_seconds <= 1200:
        duration_bucket = "10_to_20m"
    else:
        duration_bucket = "20m_plus"

    hour = row["published_at_kst_dt"].hour
    if 0 <= hour <= 5:
        upload_time_bucket = "dawn"
    elif 6 <= hour <= 11:
        upload_time_bucket = "morning"
    elif 12 <= hour <= 17:
        upload_time_bucket = "afternoon"
    elif 18 <= hour <= 21:
        upload_time_bucket = "evening"
    else:
        upload_time_bucket = "night"

    dow = row["published_at_kst_dt"].isoweekday()

    return {
        "video_id": row["video_id"],
        "channel_id": row["channel_id"],
        "category_id": row["category_id"],
        "title": row["title"],
        "published_at_utc": row["published_at_utc_raw"],
        "published_at_kst": row["published_at_kst_raw"],
        "published_date_kst": row["published_at_kst_dt"].date().isoformat(),
        "published_hour_kst": hour,
        "published_day_of_week": dow,
        "published_day_of_week_ko": DOW_KO[dow],
        "duration_seconds": duration_seconds,
        "video_type": row["video_type"],
        "view_count": view_count,
        "like_count": like_count,
        "comment_count": comment_count,
        "subscriber_count_at_collection": subscriber_count,
        "collected_at_utc": row["collected_at_utc_raw"],
        "collected_date": row["collected_date"].isoformat(),
        "video_age_days": video_age_days,
        "views_per_day": views_per_day,
        "like_rate": like_rate,
        "comment_rate": comment_rate,
        "views_per_subscriber": views_per_subscriber,
        "subscriber_segment": subscriber_segment,
        "duration_bucket": duration_bucket,
        "upload_time_bucket": upload_time_bucket,
    }


def build_channel_row(row, thumb_by_channel=None):
    # channel_thumbnail_url은 "채널의 전체 최신 수집 행"과 다른 기준으로 보완한다.
    # dedup_latest(all_prepared, channel_id)가 고르는 행은 그 채널이 마지막으로
    # 수집된 시점 기준 전체 필드 스냅샷일 뿐이라, 2026-09-03 수집기 수정 이후로
    # 그 채널의 새 영상이 한 번도 수집되지 않았으면 최신 행 자체가 옛날 데이터라
    # channel_thumbnail_url이 비어있을 수 있다(다른 필드는 최신인데 썸네일만
    # 옛날 값인 게 아니라, 애초에 그 채널의 마지막 수집 자체가 옛날이라는 뜻).
    # thumb_by_channel은 "같은 채널의 여러 수집 행 중 썸네일이 있는 행만 모아서
    # 그 중 최신"으로 별도로 구한 값 - 다른 카테고리/시점에라도 이 채널 썸네일이
    # 한 번이라도 수집된 적이 있으면 그 값을 쓴다. (Bronze 폴백은 Lambda 배포
    # zip에 outputs/bronze_collect/가 포함되지 않아 실제로는 작동하지 않는다 -
    # build_dashboard_data.py의 load_channel_avatars() 참고.)
    thumb = row["channel_thumbnail_url"]
    if not thumb and thumb_by_channel:
        thumb = thumb_by_channel.get(row["channel_id"])
    return {
        "channel_id": row["channel_id"],
        "channel_title": row["channel_name"] or row["channel_id"],
        "channel_published_at": row["channel_published_at_utc"],
        "subscriber_count": row["subscriber_count_at_collection"],
        "hidden_subscriber_count": row["hidden_subscriber_count"],
        "channel_view_count": row["channel_total_view_count"],
        "channel_video_count": row["channel_total_video_count"],
        "uploads_playlist_id": row["uploads_playlist_id"],
        "channel_thumbnail_url": thumb,
        "last_collected_at_utc": row["collected_at_utc_raw"],
    }


# ---------------------------------------------------------------------------
# 저장
# ---------------------------------------------------------------------------

def json_default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(f"Not JSON serializable: {o!r}")


def save(name, data):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=json_default, indent=2)
    print(f"  {name}: {len(data)}건 -> {path}")


def main():
    ap = argparse.ArgumentParser()
    # os.getenv("X", 기본값)은 환경변수가 "아예 없을 때"만 기본값을 쓴다. 그런데 GitHub
    # Actions의 ${{ vars.X }}는 등록 안 된 저장소 변수를 빈 문자열("")로 채워서 넘기므로,
    # env: AWS_S3_SILVER_BUCKET: ${{ vars.AWS_S3_SILVER_BUCKET }} 처럼 써두면 변수가
    # 미등록 상태여도 os.getenv에는 ""(빈 문자열, "없음"이 아님)가 전달돼 기본값이 죽는다.
    # `os.getenv("X") or 기본값`으로 빈 문자열도 "없음"으로 취급해야 "선택 항목, 기본값이
    # 실제 버킷명과 같아서 안 넣어도 됨"이라는 문서화된 의도대로 동작한다.
    ap.add_argument("--region", default=os.getenv("AWS_DEFAULT_REGION") or DEFAULT_REGION)
    ap.add_argument("--silver-bucket", default=os.getenv("AWS_S3_SILVER_BUCKET") or DEFAULT_SILVER_BUCKET)
    ap.add_argument("--gold-bucket", default=os.getenv("AWS_S3_GOLD_BUCKET") or DEFAULT_GOLD_BUCKET)
    ap.add_argument("--athena-database", default=os.getenv("AWS_ATHENA_DATABASE") or DEFAULT_ATHENA_DATABASE)
    ap.add_argument("--athena-output-location", default=os.getenv("AWS_ATHENA_OUTPUT_LOCATION") or None)
    ap.add_argument("--local-silver-path", help="S3 대신 로컬 폴더에서 Silver jsonl을 읽는 테스트 모드")
    ap.add_argument("--local-gold-path", help="S3 대신 로컬 폴더에서 Gold json을 읽는 테스트 모드 (선택)")
    args = ap.parse_args()
    if not args.athena_output_location:
        args.athena_output_location = f"s3://{args.gold_bucket}/{DEFAULT_ATHENA_OUTPUT_PREFIX}"

    use_s3 = args.local_silver_path is None
    s3 = None
    athena = None
    if use_s3:
        import boto3
        s3 = boto3.client("s3", region_name=args.region)
        athena = boto3.client("athena", region_name=args.region)
        print(f"S3 접속: silver=s3://{args.silver_bucket} gold=s3://{args.gold_bucket} region={args.region}")
        print(f"Athena 접속: database={args.athena_database} output={args.athena_output_location}")
    else:
        print(f"로컬 테스트 모드: silver={args.local_silver_path} gold={args.local_gold_path or '(없음)'}")

    all_prepared = []  # 카테고리 무관, dim_channel용
    per_category_prepared = {}  # cat_key -> [row, ...]

    for cat_key, cat_id in CATEGORY_SLUGS.items():
        raw_iter = (
            iter_s3_silver_jsonl(s3, args.silver_bucket, cat_key)
            if use_s3
            else iter_local_silver_jsonl(args.local_silver_path, cat_key)
        )
        prepared = []
        seen, skipped = 0, 0
        for rec in raw_iter:
            seen += 1
            row = transform_record(rec)
            if row is None:
                skipped += 1
                continue
            prepared.append(row)
        prepared = dedup_latest(prepared, lambda r: (r["video_id"], r["collected_date"]))
        per_category_prepared[cat_key] = prepared
        all_prepared.extend(prepared)
        print(f"Silver({cat_key}): 읽음 {seen}건 / 유효 {len(prepared)}건(중복 스냅샷 제거 후) / 스킵 {skipped}건")

    print("dim_channel 구성 (채널별 최신 수집 레코드 기준):")
    channel_rows = dedup_latest(all_prepared, lambda r: r["channel_id"])
    # 썸네일은 "채널당 전체 최신 행" 기준이 아니라 "썸네일이 있는 행 중 최신" 기준으로
    # 따로 보완한다 - build_channel_row()의 주석 참고.
    thumb_rows = [r for r in all_prepared if r.get("channel_thumbnail_url")]
    latest_thumb_rows = dedup_latest(thumb_rows, lambda r: r["channel_id"])
    thumb_by_channel = {r["channel_id"]: r["channel_thumbnail_url"] for r in latest_thumb_rows}
    save("dim_channel.json", [build_channel_row(r, thumb_by_channel) for r in channel_rows])

    print("Silver (vw_video_analysis 재현, 카테고리별):")
    for cat_key in CATEGORY_SLUGS:
        video_rows = [build_video_row(r) for r in per_category_prepared[cat_key]]
        video_rows = [v for v in video_rows if v is not None]
        save(f"video_analysis_{cat_key}.json", video_rows)

    print("Gold (analysis_week 버저닝 테이블, Athena로 조회):")
    for table in GOLD_VERSIONED_TABLES:
        if use_s3:
            rows = query_athena_gold_table(athena, args.athena_database, table, args.athena_output_location)
        else:
            rows = load_local_gold_json(args.local_gold_path, table) if args.local_gold_path else []
        save(f"{table}.json", rows)

    print("DONE")


if __name__ == "__main__":
    main()
