#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lambda/refresh_dashboard.py
----------------------------
GitHub Actions(refresh-dashboard-data.yml) 대신 이 프로젝트가 이미 쓰고 있는
EventBridge+Lambda 패턴(gold_compute_athena.py와 동일)으로, Silver/Gold ->
frontend/mock/*.json -> 프론트엔드 S3 버킷 -> CloudFront 무효화까지 전부 처리한다.

GitHub Actions를 쓰던 이유는 "mock/*.json을 git에 커밋해서 다들 pull 받아 보게"
하려던 거였는데, CloudFront 배포(infra/frontend.tf) 이후로는 그 이유가 사라졌고,
대신 AWS 자격증명을 GitHub Secrets에 export해야 하는 부담만 남았다. Lambda는
실행 역할(aws_iam_role.lambda, IAM Role)로 AWS 인증을 하므로 자격증명을 어디에도
등록할 필요가 없다 - 이 프로젝트의 다른 모든 스케줄 작업(daily_search_collector,
gold_compute_athena 등)과 같은 방식.

기존 스크립트를 다시 쓰지 않고 그대로 재사용한다:
  frontend/scripts/export_s3_for_dashboard.py - Silver(S3 직접) + Gold(Athena) 읽기
  frontend/scripts/build_dashboard_data.py    - 그 결과로 frontend/mock/*.json 생성
둘 다 이미 실제 AWS로 검증된 스크립트라 로직을 복제/재작성하지 않고, subprocess로
그대로 실행한다. 단, 두 스크립트 모두 자기 파일 위치(__file__)를 기준으로
outputs/, frontend/mock/ 경로를 계산하는데, Lambda 배포 패키지(/var/task)는
읽기 전용이라 그 경로에 못 쓴다 - 그래서 실행 전에 /tmp(Lambda에서 쓰기 가능한
유일한 영역)로 스크립트를 복사해서, 같은 상대 경로 구조(frontend/scripts/...)를
그대로 유지한 채 그 밑에서 실행한다. 이러면 두 스크립트를 한 줄도 안 고쳐도 된다.
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
SCRIPT_NAMES = ("export_s3_for_dashboard.py", "build_dashboard_data.py")


def _prepare_writable_copy():
    """/var/task/frontend/scripts/*.py(읽기 전용, 배포 패키지) -> /tmp/app/frontend/scripts/*.py
    (쓰기 가능). 상대 위치가 같아야 두 스크립트의 __file__ 기반 경로 계산이
    /tmp/app을 ROOT로 잡는다."""
    src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "scripts")
    dst_dir = os.path.join(APP_DIR, "frontend", "scripts")
    os.makedirs(dst_dir, exist_ok=True)
    for name in SCRIPT_NAMES:
        shutil.copy(os.path.join(src_dir, name), os.path.join(dst_dir, name))
    return dst_dir


def _run_script(dst_dir, name, timeout_sec):
    result = subprocess.run(
        [sys.executable, os.path.join(dst_dir, name)],
        cwd=APP_DIR,
        env=dict(os.environ),  # Lambda 실행 역할의 임시자격증명이 이미 들어있음(boto3가 자동 사용)
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

    # 1) Silver(S3 직접) + Gold(Athena) -> /tmp/app/outputs/silver_gold_export/*.json
    _run_script(dst_dir, "export_s3_for_dashboard.py", timeout_sec=240)

    # 2) 그 결과로 frontend/mock/*.json 생성 -> /tmp/app/frontend/mock/*.json
    _run_script(dst_dir, "build_dashboard_data.py", timeout_sec=60)

    # 3) mock/*.json을 프론트엔드 S3 버킷의 /mock/ 로 업로드
    s3 = boto3.client("s3")
    mock_dir = os.path.join(APP_DIR, "frontend", "mock")
    uploaded = []
    for name in sorted(os.listdir(mock_dir)):
        if not name.endswith(".json"):
            continue
        s3.upload_file(
            os.path.join(mock_dir, name),
            FRONTEND_BUCKET,
            f"mock/{name}",
            ExtraArgs={"ContentType": "application/json"},
        )
        uploaded.append(name)
    print(f"S3 업로드 완료: {uploaded} -> s3://{FRONTEND_BUCKET}/mock/")

    # 4) CloudFront /mock/* 캐시 무효화 (frontend.tf가 /mock/*에 짧은 TTL을 걸어뒀지만,
    #    다음 요청까지 기다리지 않고 바로 반영되게 즉시 무효화)
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
