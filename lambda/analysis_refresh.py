#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lambda/analysis_refresh.py
----------------------------
spec.md 분석 5/6/7(성장곡선 유형화·판단시점·메타데이터 최적화)에 쓰던 3개 스크립트
(frontend/scripts/build_metadata_impact.py, build_topic_trends.py,
build_synthetic_demo.py)를 자동화한다. 지금까지 이 3개는 "자동화 대상 아님"으로
남겨뒀었는데(pandas/statsmodels/scikit-learn 의존성이 커서 일반 zip Lambda의
250MB 제한을 넘길 위험이 있음), 컨테이너 이미지 Lambda(최대 10GB, 이 제한이
없음)로 옮겨서 자동화한다 - lambda/refresh_dashboard.py나 gold_compute_athena.py
같은 zip 기반 Lambda와 배포 방식만 다르고, 하는 일의 성격은 동일하다.

build_metadata_impact.py/build_topic_trends.py는 원래 로컬 outputs/silver/*.jsonl만
읽게 짜여 있었는데, 이 Lambda에서 쓸 수 있도록 AWS_S3_SILVER_BUCKET 환경변수가
있으면 S3에서 직접 읽도록 두 파일 모두 수정했다(로컬 개발 시 이 환경변수가 없으면
기존처럼 로컬 파일을 그대로 읽어 로컬 워크플로는 안 깨짐). build_synthetic_demo.py는
애초에 실측 데이터를 안 써서 손댈 게 없다.

세 스크립트 모두 손대지 않고(재작성 없이) subprocess로 그대로 재사용한다 -
lambda/refresh_dashboard.py와 같은 이유로, 세 스크립트가 __file__ 기준으로
frontend/mock/ 경로를 계산해서 쓰기 때문에(배포 패키지는 읽기 전용) 실행 전
/tmp로 복사해서 그 계산이 쓰기 가능한 위치를 가리키게 만든다.
"""
import json
import os
import shutil
import subprocess
import sys

import boto3

APP_DIR = "/tmp/app"
FRONTEND_BUCKET = os.environ.get("FRONTEND_S3_BUCKET", "goldline-dev-frontend-827913617635")
CLOUDFRONT_DISTRIBUTION_ID = os.environ.get("CLOUDFRONT_DISTRIBUTION_ID", "EVDHEA0GFHDS3")
# (스크립트 파일명, 그 스크립트가 만드는 mock 파일명, 타임아웃 초) - 순서 무관, 서로 독립적
SCRIPTS = [
    ("build_metadata_impact.py", "metadata_impact.json", 180),
    ("build_topic_trends.py", "topic_trends.json", 180),
    ("build_synthetic_demo.py", "synthetic_demo.json", 60),
]


def _prepare_writable_copy():
    src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "scripts")
    dst_dir = os.path.join(APP_DIR, "frontend", "scripts")
    os.makedirs(dst_dir, exist_ok=True)
    for name, _, _ in SCRIPTS:
        shutil.copy(os.path.join(src_dir, name), os.path.join(dst_dir, name))
    return dst_dir


def _run_script(dst_dir, name, timeout_sec):
    result = subprocess.run(
        [sys.executable, os.path.join(dst_dir, name)],
        cwd=APP_DIR,
        env=dict(os.environ),  # AWS_S3_SILVER_BUCKET + Lambda 실행 역할 임시자격증명 포함
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError(f"{name} 실패(exit={result.returncode}): {result.stderr[-2000:]}")


def lambda_handler(event, context):
    dst_dir = _prepare_writable_copy()

    for name, _, timeout_sec in SCRIPTS:
        _run_script(dst_dir, name, timeout_sec)

    s3 = boto3.client("s3")
    mock_dir = os.path.join(APP_DIR, "frontend", "mock")
    uploaded = []
    for _, mock_name, _ in SCRIPTS:
        path = os.path.join(mock_dir, mock_name)
        if not os.path.exists(path):
            print(f"WARNING: {mock_name}이 생성되지 않았습니다 - 건너뜀")
            continue
        s3.upload_file(
            path, FRONTEND_BUCKET, f"mock/{mock_name}",
            ExtraArgs={"ContentType": "application/json"},
        )
        uploaded.append(mock_name)
    print(f"S3 업로드 완료: {uploaded} -> s3://{FRONTEND_BUCKET}/mock/")

    cf = boto3.client("cloudfront")
    invalidation = cf.create_invalidation(
        DistributionId=CLOUDFRONT_DISTRIBUTION_ID,
        InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": ["/mock/*"]},
            "CallerReference": context.aws_request_id,
        },
    )
    invalidation_id = invalidation["Invalidation"]["Id"]
    print(f"CloudFront invalidation 요청: {invalidation_id}")

    return {
        "statusCode": 200,
        "body": json.dumps({"uploaded": uploaded, "invalidation_id": invalidation_id}),
    }
