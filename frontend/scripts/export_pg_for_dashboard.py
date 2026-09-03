#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
frontend/scripts/export_pg_for_dashboard.py
--------------------------------------------
대시보드(build_dashboard_data.py)가 Silver/Gold를 쓸 수 있도록,
PostgreSQL에서 필요한 데이터를 로컬 JSON 파일로 내려받는 스크립트입니다.

전제 조건 (로컬에서 아래가 이미 되어 있어야 함):
  1) docker-compose up -d postgres  (또는 airflow까지 전부)
  2) sql/youtube_pipeline_schema_postgresql.sql 적용
  3) transforms/load_silver_to_postgres.py 로 Silver 데이터 적재
  4) sql/compute_gold.sql 실행 (Gold 테이블 채우기)
  => 이 스크립트는 "읽기 전용"입니다. 3), 4)가 아직 안 되어 있으면 그냥 빈 배열이
     저장됩니다 (에러는 아님) — 그 경우 build_dashboard_data.py가 자동으로
     Bronze로 폴백합니다.

사용:
  pip install -r requirements.txt   # psycopg2-binary 포함되어 있음
  python frontend/scripts/export_pg_for_dashboard.py

접속 정보는 환경변수(PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE)로 바꿀 수 있고,
기본값은 docker-compose.yml의 로컬 개발용 값과 동일합니다(host=localhost,
port=5432, db/user/password=airflow). AWS RDS를 쓰는 팀원은 그에 맞게
환경변수만 바꿔서 실행하면 됩니다.

결과: outputs/silver_gold_export/ 아래에
  video_analysis_{gaming,autos_vehicles,film_animation}.json  (Silver 뷰, 영상 단위)
  dim_channel.json                                            (Silver, 채널 단위)
  gold_category_benchmark.json / gold_upload_strategy.json /
  gold_new_creator_guide.json / gold_video_rank_trend.json    (Gold 4종)
"""
import json
import os
from datetime import date, datetime
from decimal import Decimal

import psycopg2
import psycopg2.extras

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.dirname(SCRIPT_DIR)
ROOT = os.path.dirname(FRONTEND_DIR)
OUT_DIR = os.path.join(ROOT, "outputs", "silver_gold_export")

CATEGORIES = {"gaming": "20", "autos_vehicles": "2", "film_animation": "1"}


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "airflow"),
        user=os.getenv("PGUSER", "airflow"),
        password=os.getenv("PGPASSWORD", "airflow"),
    )


def json_default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(f"Not JSON serializable: {o!r}")


def dump(cur, sql, params=None):
    cur.execute(sql, params or ())
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def save(name, data):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=json_default, indent=2)
    print(f"  {name}: {len(data)}건 -> {path}")


def main():
    print(f"접속: {os.getenv('PGHOST', 'localhost')}:{os.getenv('PGPORT', '5432')}/{os.getenv('PGDATABASE', 'airflow')}")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path TO youtube_analytics, public;")

            print("Silver (vw_video_analysis, 카테고리별):")
            for cat_key, cat_id in CATEGORIES.items():
                rows = dump(cur, "SELECT * FROM vw_video_analysis WHERE category_id = %s", (cat_id,))
                save(f"video_analysis_{cat_key}.json", rows)

            print("Silver (dim_channel):")
            save("dim_channel.json", dump(cur, "SELECT * FROM dim_channel"))

            print("Gold (analysis_week 버저닝 테이블):")
            for table in ("gold_category_benchmark", "gold_upload_strategy", "gold_new_creator_guide"):
                rows = dump(cur, f"SELECT * FROM {table} ORDER BY analysis_week DESC")
                save(f"{table}.json", rows)

            print("Gold (video_rank_trend, 현재 상태 테이블):")
            save("gold_video_rank_trend.json", dump(cur, "SELECT * FROM gold_video_rank_trend"))
    finally:
        conn.close()

    print("DONE")


if __name__ == "__main__":
    main()
