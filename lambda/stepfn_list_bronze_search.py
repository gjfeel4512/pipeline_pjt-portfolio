#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step Functions 파이프라인(search_to_silver_stepfn)의 1단계 Lambda.

dags/search_bronze_to_silver_dag.py의 list_bronze_search_objects() 태스크와 동일한 일을
Airflow/로컬 없이 한다: 오늘(KST) 날짜 파티션의 bronze/search/category=<slug>/... 안에서
아직 처리 대상인 .jsonl 오브젝트 키 목록을 뽑아 Step Functions의 다음 단계(Map 상태)로
넘긴다.

이벤트: {} (입력 불필요, EventBridge 스케줄이 그냥 트리거)
반환:   {"keys": ["bronze/search/category=gaming/year=2026/month=09/day=03/....jsonl", ...]}
"""
import os
from datetime import datetime, timedelta, timezone

import boto3

AWS_S3_BRONZE_BUCKET = os.environ["BUCKET_NAME"]
KST = timezone(timedelta(hours=9))

# lambda/youtube_api_daily.py의 CATEGORY_SLUGS와 동일
CATEGORY_SLUGS = ['film_animation', 'autos_vehicles', 'gaming', 'people_blogs']


def lambda_handler(event, context):
    s3 = boto3.client("s3")
    today = datetime.now(KST)
    year, month, day = today.strftime("%Y"), today.strftime("%m"), today.strftime("%d")

    keys = []
    for slug in CATEGORY_SLUGS:
        prefix = f"bronze/search/category={slug}/year={year}/month={month}/day={day}/"
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=AWS_S3_BRONZE_BUCKET, Prefix=prefix):
            keys.extend(
                obj["Key"] for obj in page.get("Contents", []) if obj["Key"].endswith(".jsonl")
            )

    print(f"Found {len(keys)} bronze/search objects for {year}-{month}-{day}")
    return {"keys": keys}
