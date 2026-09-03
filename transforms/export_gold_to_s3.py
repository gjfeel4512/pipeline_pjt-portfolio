#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PostgreSQL gold_* 테이블 -> S3 Gold 버킷 JSON 내보내기.

sql/compute_gold.sql 실행 직후 사용. PostgreSQL이 Gold의 실제 조회 대상(analysis
목적)이고, S3는 원본 보관/재현·백업/Athena 등 다른 도구에서도 읽을 수 있게 하는
아카이브 사본이다(다이어그램의 "s3://bucket/gold/ <-> PostgreSQL" 양방향 화살표).

사용:
  python transforms/export_gold_to_s3.py --bucket goldline-dev-gold-827913617635
"""
import argparse
import json
import os
from datetime import date, datetime
from decimal import Decimal

import psycopg2
import psycopg2.extras


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


TABLES = ["gold_category_benchmark", "gold_upload_strategy", "gold_new_creator_guide"]


def export_table(cur, table, analysis_week):
    cur.execute(
        f"SELECT * FROM youtube_analytics.{table} WHERE analysis_week = %s",
        (analysis_week,),
    )
    cols = [d.name for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    # analysis_week은 S3 파티션 키(analysis_week=.../)로 이미 인코딩돼 있음 - 데이터 파일
    # 안에도 같은 이름의 컬럼이 있으면 Athena/Glue가 파티션 컬럼 중복으로 보고 그 파일을
    # 조용히 무시함(쿼리 결과가 항상 0행). 그래서 여기서 제거.
    for row in rows:
        row.pop("analysis_week", None)
    return rows


def upload_json(s3, bucket, key, rows):
    """JSON Lines(한 줄 = 레코드 하나)로 저장 - Athena JSON SerDe가 배열이 아니라
    이 형식만 읽을 수 있음(Silver 파일들과 동일 관례)."""
    body = "\n".join(json.dumps(r, ensure_ascii=False, default=json_default) for r in rows)
    if rows:
        body += "\n"
    s3.put_object(Bucket=bucket, Key=key, Body=body.encode("utf-8"), ContentType="application/x-ndjson")
    return len(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", required=True, help="Gold S3 버킷명")
    ap.add_argument("--analysis-week", help="YYYY-MM-DD (생략 시 이번 주 월요일)")
    args = ap.parse_args()

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if args.analysis_week:
                analysis_week = args.analysis_week
            else:
                cur.execute("SELECT date_trunc('week', CURRENT_DATE)::date")
                analysis_week = cur.fetchone()[0].isoformat()

            import boto3
            s3 = boto3.client("s3", region_name=os.getenv("AWS_DEFAULT_REGION", "us-west-2"))

            for table in TABLES:
                rows = export_table(cur, table, analysis_week)
                key = f"{table}/analysis_week={analysis_week}/{table}.jsonl"
                size = upload_json(s3, args.bucket, key, rows)
                print(f"{table}: {len(rows)}건 -> s3://{args.bucket}/{key} ({size} bytes)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
