#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/reviewer_review_start.py
-------------------------------------
infra/reviewer_candidates_review.tf의 reviewer_candidate_review 상태머신을 새로
시작한다. 이 상태머신은 자동 스케줄이 없다(사람이 검토하고 싶을 때만 시작하는 게
맞다고 판단해 EventBridge cron을 안 붙였다) - 그래서 실행은 항상 이 스크립트(또는
AWS 콘솔/CLI)로 사람이 직접 트리거해야 한다.

시작 후 상태머신은:
  1) GenerateCandidates - Silver-rejected(category_id=22)를 스캔해 후보 계산
  2) WaitForReview       - scripts/reviewer_review_submit.py로 결정을 제출할 때까지
                            대기(최대 30일). SNS 구독이 돼있으면 이때 이메일이 감.
  3) ApplyDecisions      - 승인된 것만 overrides/channel_category_override.json에 반영

주의: WaitForReview에 결과를 보내는(SendTaskSuccess) 건 이 스크립트가 아니라
scripts/reviewer_review_submit.py다. 그 스크립트를 실행할 사람의 AWS 자격증명에
states:SendTaskSuccess / states:SendTaskFailure 권한이 있어야 한다(이 상태머신
ARN에 대해) - infra/의 서비스 역할과는 별개로 IAM에서 챙겨줘야 함.

사용법:
  python scripts/reviewer_review_start.py
  python scripts/reviewer_review_start.py --state-machine-arn arn:aws:states:...
"""
import argparse
import datetime
import json
import subprocess
import sys

import boto3


def resolve_state_machine_arn():
    """infra/ 에서 terraform output으로 ARN을 직접 읽어본다 (편의용, 실패해도 무방)."""
    try:
        out = subprocess.run(
            ["terraform", "output", "-raw", "reviewer_candidate_review_state_machine_arn"],
            cwd="infra", capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, check=True,
        )
        arn = out.stdout.strip()
        return arn or None
    except Exception:
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state-machine-arn", default=None, help="미지정 시 infra/ 에서 terraform output으로 자동 조회 시도")
    p.add_argument("--region", default="us-west-2")
    args = p.parse_args()

    arn = args.state_machine_arn or resolve_state_machine_arn()
    if not arn:
        print("state machine ARN을 찾을 수 없습니다. --state-machine-arn으로 직접 넘겨주세요.", file=sys.stderr)
        print("(repo 루트에서: cd infra && terraform output reviewer_candidate_review_state_machine_arn)", file=sys.stderr)
        sys.exit(1)

    sfn = boto3.client("stepfunctions", region_name=args.region)
    name = f"reviewer-review-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    resp = sfn.start_execution(stateMachineArn=arn, name=name, input=json.dumps({}))
    print(f"실행 시작: {resp['executionArn']}")
    print("GenerateCandidates가 끝나면(보통 수십 초~수 분) SNS 알림(구독돼 있으면 이메일)이 갑니다.")
    print("검토 준비가 되면: python scripts/reviewer_review_submit.py --batch-id <batch_id>")
    print("(batch_id는 SNS 알림 본문 또는 Step Functions 콘솔의 실행 입력/로그에서 확인 가능)")


if __name__ == "__main__":
    main()
