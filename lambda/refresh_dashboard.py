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
  frontend/scripts/export_s3_for_dashboard.py       - Silver(S3 직접) + Gold(Athena) 읽기
  frontend/scripts/build_dashboard_data.py          - 그 결과로 frontend/mock/*.json 생성
  frontend/scripts/build_cross_category_reviewers.py - Silver의 격리된 people_blogs
                                                        reject 레코드를 스캔해서
                                                        frontend/mock/cross_category_reviewers.json
                                                        (대시보드 "리뷰어 후보" 탭)을 생성
셋 다 이미 실제 AWS로 검증된 스크립트라 로직을 복제/재작성하지 않고, subprocess로
그대로 실행한다. 단, 세 스크립트 모두 자기 파일 위치(__file__)를 기준으로
outputs/, frontend/mock/ 경로를 계산하는데, Lambda 배포 패키지(/var/task)는
읽기 전용이라 그 경로에 못 쓴다 - 그래서 실행 전에 /tmp(Lambda에서 쓰기 가능한
유일한 영역)로 스크립트를 복사해서, 같은 상대 경로 구조(frontend/scripts/...)를
그대로 유지한 채 그 밑에서 실행한다. 이러면 세 스크립트를 한 줄도 안 고쳐도 된다.

2026-09-07: build_cross_category_reviewers.py를 이 흐름에 새로 추가했다. 전에는
어떤 자동화 경로(수동 Lambda 호출이든 pipeline_orchestrator 4시간 주기든)도 이
스크립트를 호출하지 않아서, 사람이 Step Functions reviewer_candidate_review
워크플로로 채널을 승인/거부해 review/reviewed_channels.json이 갱신돼도, 대시보드
"리뷰어 후보" 탭이 읽는 frontend/mock/cross_category_reviewers.json은 아무도 다시
만들지 않아 계속 예전 후보 목록 그대로 남아 있었다. 이 스크립트는 로컬
outputs/silver/rejected/*.jsonl을 전제로 만들어졌지만(--silver-bucket 옵션 추가 전),
Lambda의 /tmp에는 그 파일이 없으므로 --silver-bucket을 줘서 S3를 직접 스캔하게 한다
(lambda/reviewer_candidates_generate.py와 동일한 스캔 방식).
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
SCRIPT_NAMES = ("export_s3_for_dashboard.py", "build_dashboard_data.py", "build_cross_category_reviewers.py")

# 방금 만든 mock에 실제로 노출되는 video_id 전부를 여기에 써두면, daily_search_collector가
# 매 실행마다 이걸 읽어서 그 영상들 조회수를 계속 갱신한다(백필/스테디로 known_videos에서
# 빠진 영상도 화면에 떠 있는 동안은 라이브). daily_search_collector의 PINNED_KEY와 동일 경로.
BRONZE_BUCKET = os.environ.get("AWS_S3_BRONZE_BUCKET", "goldline-dev-bronze-827913617635")
# build_cross_category_reviewers.py에 --gold-bucket/--silver-bucket으로 그대로 넘길 값.
# 이 Lambda의 환경변수(infra/refresh_dashboard.tf)에 이미 있는 것과 동일한 버킷.
GOLD_BUCKET = os.environ.get("AWS_S3_GOLD_BUCKET")
SILVER_BUCKET = os.environ.get("AWS_S3_SILVER_BUCKET")
PINNED_KEY = "bronze/_checkpoints/pinned_video_ids.json"
SLUG_TO_LABEL = {
    "film_animation": "영화_애니메이션",
    "autos_vehicles": "자동차_차량",
    "gaming": "게임",
    "people_blogs": "인물_블로그",
}


def _collect_video_ids(mock_dir):
    """mock/*.json 안의 모든 video_id를 {video_id: category_label}로 모은다.
    video_pool.json은 카테고리 슬러그로 키가 나뉘어 있어 라벨을 정확히 붙일 수 있고,
    나머지(channel_pool, history_replay 등)는 라벨 ""로 둔다(수집기가 videos.list의
    실제 categoryId로 파티션을 정함)."""
    def walk(node):
        out = []
        if isinstance(node, dict):
            vid = node.get("video_id")
            if isinstance(vid, str) and vid:
                out.append(vid)
            for v in node.values():
                out += walk(v)
        elif isinstance(node, list):
            for v in node:
                out += walk(v)
        return out

    pinned = {}
    for name in os.listdir(mock_dir):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(mock_dir, name), encoding="utf-8") as f:
                data = json.load(f)
        except (ValueError, OSError):
            continue
        if name == "video_pool.json" and isinstance(data, dict):
            for slug, rows in data.items():
                label = SLUG_TO_LABEL.get(slug, "")
                for vid in walk(rows):
                    pinned[vid] = pinned.get(vid) or label
        else:
            for vid in walk(data):
                pinned.setdefault(vid, "")
    return pinned


def _prepare_writable_copy():
    """/var/task/frontend/scripts/*.py(읽기 전용, 배포 패키지) -> /tmp/app/frontend/scripts/*.py
    (쓰기 가능). 상대 위치가 같아야 스크립트들의 __file__ 기반 경로 계산이
    /tmp/app을 ROOT로 잡는다."""
    src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "scripts")
    dst_dir = os.path.join(APP_DIR, "frontend", "scripts")
    os.makedirs(dst_dir, exist_ok=True)
    for name in SCRIPT_NAMES:
        shutil.copy(os.path.join(src_dir, name), os.path.join(dst_dir, name))
    return dst_dir


def _run_script(dst_dir, name, timeout_sec, extra_args=None):
    result = subprocess.run(
        [sys.executable, os.path.join(dst_dir, name), *(extra_args or [])],
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

    # 2.5) 대시보드 "리뷰어 후보" 탭: Silver의 격리된 people_blogs reject 레코드를 S3에서
    #      직접 스캔해서 frontend/mock/cross_category_reviewers.json을 다시 만든다.
    #      --gold-bucket을 줘서 review/reviewed_channels.json(사람이 이미 승인/거부한
    #      채널)을 반영하고, --silver-bucket을 줘서 로컬 파일 없이 /tmp 환경에서도
    #      동작하게 한다.
    _run_script(
        dst_dir,
        "build_cross_category_reviewers.py",
        timeout_sec=120,
        extra_args=["--gold-bucket", GOLD_BUCKET, "--silver-bucket", SILVER_BUCKET],
    )

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

    # 3.5) 대시보드에 노출되는 video_id 전부를 핀 목록으로 갱신(덮어쓰기).
    #      daily_search_collector가 다음 실행부터 이걸 읽어 해당 영상 조회수를 계속 갱신.
    try:
        pinned = _collect_video_ids(os.path.join(APP_DIR, "frontend", "mock"))
        s3.put_object(
            Bucket=BRONZE_BUCKET,
            Key=PINNED_KEY,
            Body=json.dumps(dict(sorted(pinned.items())), ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
        print(f"핀 목록 갱신: {len(pinned)}개 -> s3://{BRONZE_BUCKET}/{PINNED_KEY}")
    except Exception as e:  # 핀 목록 실패가 대시보드 갱신 전체를 깨뜨리진 않게
        print(f"핀 목록 갱신 실패(무시하고 계속): {e}")

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
