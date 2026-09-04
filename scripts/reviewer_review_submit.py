#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/reviewer_review_submit.py
--------------------------------------
reviewer_candidate_review 상태머신이 WaitForReview에서 멈춰있는 배치를 사람이
검토하고, 승인/거부를 일괄로 제출한다(SendTaskSuccess) - 그러면 상태머신이 이어서
ApplyDecisions를 실행해 승인된 것만 overrides/channel_category_override.json에
반영한다.

필요 권한: 이 스크립트를 실행하는 AWS 자격증명(IAM 사용자/역할)에
states:SendTaskSuccess / states:SendTaskFailure 권한이 있어야 한다(대상은 이
상태머신의 실행 ARN, 보통 상태머신 ARN 전체에 대해 부여). infra/의 서비스 역할과는
별개 - 이건 "사람"이 콜백을 보내는 것이므로 사람 쪽 IAM에서 챙겨줘야 한다.

세 가지 사용 방식:
  1) --download-only : candidates.json만 내려받고 종료 (검토 준비, 아직 제출 안 함)
  2) 대화형(기본)      : 후보를 하나씩 보여주고 y(승인)/n(거부)/q(중단)로 입력받는다
  3) --decisions-file : --download-only로 받은 파일을 사람이 직접 열어 각 항목에
                        "approved": true/false를 채워넣은 뒤 그 파일로 일괄 제출
                        (후보가 많을 때 편집기로 한 번에 검토하고 싶은 경우)

사용법:
  python scripts/reviewer_review_submit.py --batch-id batch-20260904-013000 --download-only
  python scripts/reviewer_review_submit.py --batch-id batch-20260904-013000
  python scripts/reviewer_review_submit.py --batch-id batch-20260904-013000 --decisions-file batch-20260904-013000_candidates.json
  python scripts/reviewer_review_submit.py --batch-id batch-20260904-013000 --abort   # 검토를 아예 취소(실행을 실패로 종료)
"""
import argparse
import json
import subprocess
import sys

import boto3
from botocore.exceptions import ClientError

REVIEW_PREFIX_ROOT = "review/cross_category_reviewers"


def get_gold_bucket(region):
    out = subprocess.run(
        ["terraform", "output", "-json", "s3_bucket_names"],
        cwd="infra", capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, check=True,
    )
    return json.loads(out.stdout)["gold"]


def load_json_from_s3(s3, bucket, key):
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise


def interactive_review(candidates):
    decisions = []
    print(f"\n총 {len(candidates)}건의 후보를 검토합니다. (y=승인 / n=거부 / q=중단, 여기까지만 반영)\n")
    for i, c in enumerate(candidates, 1):
        print(f"[{i}/{len(candidates)}] {c['channel_name']}  (channel_id={c['channel_id']})")
        print(f"   추천 카테고리: {c['suggested_category_label']} ({c['suggested_category']}) · 매칭 영상 {c['matched_video_count']}건")
        for t in c.get("sample_titles", []):
            print(f"   - {t}")
        while True:
            ans = input("   승인하시겠습니까? [y/n/q]: ").strip().lower()
            if ans in ("y", "n", "q"):
                break
            print("   y, n, q 중 하나만 입력해주세요.")
        if ans == "q":
            print(f"\n중단합니다. 지금까지 결정한 {len(decisions)}건만 제출합니다 (나머지는 이번 배치에 포함되지 않음).")
            break
        decisions.append({
            "channel_id": c["channel_id"],
            "channel_name": c["channel_name"],
            "approved": ans == "y",
            "category": c["suggested_category"],
            "category_label": c["suggested_category_label"],
            "matched_video_count": c["matched_video_count"],
        })
    return decisions


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--batch-id", required=True)
    p.add_argument("--region", default="us-west-2")
    p.add_argument("--download-only", action="store_true", help="candidates.json만 내려받고 종료")
    p.add_argument("--decisions-file", default=None, help="approved 필드를 채워둔 candidates.json 형식 파일로 일괄 제출")
    p.add_argument("--abort", action="store_true", help="검토를 취소하고 상태머신 실행을 실패로 종료(SendTaskFailure)")
    args = p.parse_args()

    bucket = get_gold_bucket(args.region)
    s3 = boto3.client("s3", region_name=args.region)
    sfn = boto3.client("stepfunctions", region_name=args.region)

    session_key = f"{REVIEW_PREFIX_ROOT}/{args.batch_id}/review_session.json"
    session = load_json_from_s3(s3, bucket, session_key)
    if session is None:
        print(f"검토 세션을 찾을 수 없습니다: s3://{bucket}/{session_key}", file=sys.stderr)
        print("이미 검토를 제출/취소했거나(review_session.json은 그때 삭제됨), batch-id가 틀렸을 수 있습니다.", file=sys.stderr)
        sys.exit(1)

    if args.abort:
        sfn.send_task_failure(
            taskToken=session["task_token"],
            error="ReviewAborted",
            cause="사람이 검토를 취소함 (scripts/reviewer_review_submit.py --abort)",
        )
        s3.delete_object(Bucket=bucket, Key=session_key)
        print(f"배치 {args.batch_id} 검토를 취소했습니다. 상태머신 실행은 실패로 종료됩니다.")
        return

    candidates_doc = load_json_from_s3(s3, bucket, session["candidates_key"])
    candidates = candidates_doc["candidates"]

    local_path = f"{args.batch_id}_candidates.json"
    with open(local_path, "w", encoding="utf-8") as f:
        json.dump(candidates_doc, f, ensure_ascii=False, indent=2)
    print(f"후보 {len(candidates)}건을 {local_path}에 저장했습니다.")

    if args.download_only:
        print("--download-only 이므로 여기서 종료합니다. 검토 후 다시 실행하거나 --decisions-file로 제출하세요.")
        return

    if args.decisions_file:
        with open(args.decisions_file, encoding="utf-8") as f:
            edited = json.load(f)
        edited_candidates = edited["candidates"] if isinstance(edited, dict) else edited
        decisions = []
        for c in edited_candidates:
            if "approved" not in c:
                print(f"경고: {c.get('channel_id')}에 approved 필드가 없어 거부로 처리합니다.", file=sys.stderr)
            decisions.append({
                "channel_id": c["channel_id"],
                "channel_name": c.get("channel_name"),
                "approved": bool(c.get("approved", False)),
                "category": c.get("category") or c.get("suggested_category"),
                "category_label": c.get("category_label") or c.get("suggested_category_label"),
                "matched_video_count": c.get("matched_video_count"),
            })
    else:
        decisions = interactive_review(candidates)

    if not decisions:
        print("결정된 항목이 없어 제출하지 않습니다. (검토 세션은 계속 대기 상태로 남습니다)")
        return

    approved_count = sum(1 for d in decisions if d["approved"])
    print(f"\n승인 {approved_count}건 / 거부 {len(decisions) - approved_count}건을 제출합니다...")

    sfn.send_task_success(
        taskToken=session["task_token"],
        output=json.dumps({"batch_id": args.batch_id, "decisions": decisions}, ensure_ascii=False),
    )
    print("제출 완료 - 상태머신이 ApplyDecisions를 이어서 실행합니다 (몇 초 내).")
    print(f"결과 확인: s3://{bucket}/{REVIEW_PREFIX_ROOT}/{args.batch_id}/decisions.json")
    print(f"반영된 오버라이드 전체 목록: s3://{bucket}/overrides/channel_category_override.json")


if __name__ == "__main__":
    main()
