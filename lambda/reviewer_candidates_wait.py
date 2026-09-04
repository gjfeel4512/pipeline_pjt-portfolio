#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lambda/reviewer_candidates_wait.py
---------------------------------------
reviewer_candidate_review 상태머신의 두 번째 Task(.waitForTaskToken) - 사람이
검토를 마치고 scripts/reviewer_review_submit.py로 SendTaskSuccess를 보낼 때까지
상태머신을 여기서 멈춰 세운다.

이 Lambda 자신은 SendTaskSuccess를 호출하지 않는다(그게 이 콜백 패턴의 핵심) - 할
일은 딱 두 가지뿐이다:
  1) 콜백 토큰(TaskToken)을 나중에 scripts/reviewer_review_submit.py가 다시 읽을 수
     있도록 S3(review_session.json)에 남겨둔다.
  2) SNS로 "검토 대기 중" 알림을 보낸다(구독돼 있으면 이메일로 감).
그 다음 그냥 정상 반환한다 - Step Functions는 이 Lambda의 반환값과 무관하게, 외부에서
SendTaskSuccess/SendTaskFailure가 올 때까지 이 Task 상태에 계속 머무른다
(infra/reviewer_candidates_review.tf의 TimeoutSeconds가 최대 대기 시간, 기본 30일).

환경변수:
  GOLD_BUCKET_NAME  review_session.json을 쓸 S3 버킷
  SNS_TOPIC_ARN     검토 대기 알림을 보낼 SNS 토픽 (없으면 알림 생략)
"""
import datetime
import json
import os

import boto3

GOLD_BUCKET_NAME = os.environ.get("GOLD_BUCKET_NAME")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")

REVIEW_PREFIX_ROOT = "review/cross_category_reviewers"

s3 = boto3.client("s3")
sns = boto3.client("sns")


def lambda_handler(event, context):
    task_token = event["TaskToken"]
    batch_id = event["BatchId"]
    candidates_key = event["CandidatesKey"]
    candidate_count = event["CandidateCount"]

    session_key = f"{REVIEW_PREFIX_ROOT}/{batch_id}/review_session.json"
    session = {
        "task_token": task_token,
        "batch_id": batch_id,
        "candidates_key": candidates_key,
        "candidate_count": candidate_count,
        "requested_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    s3.put_object(
        Bucket=GOLD_BUCKET_NAME,
        Key=session_key,
        Body=json.dumps(session, ensure_ascii=False, indent=2).encode("utf-8"),
    )

    if SNS_TOPIC_ARN:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Subject=f"[GoldLine] 리뷰어 후보 검토 대기 중 ({candidate_count}건)",
            Message=(
                f"카테고리 재분류 후보 {candidate_count}건이 검토 대기 중입니다.\n\n"
                f"후보 목록: s3://{GOLD_BUCKET_NAME}/{candidates_key}\n\n"
                f"검토하려면 로컬에서 다음을 실행하세요:\n"
                f"  python scripts/reviewer_review_submit.py --batch-id {batch_id}\n\n"
                f"검토(승인/거부)가 없으면 이 실행은 최대 30일간 대기하다가 타임아웃됩니다."
            ),
        )

    # SendTaskSuccess를 여기서 호출하지 않는다 - 상태머신은 review_submit.py가
    # 콜백을 보낼 때까지(또는 TimeoutSeconds 만료까지) WaitForReview Task에 멈춰있는다.
    return {"session_key": session_key}
