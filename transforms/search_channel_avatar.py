#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
outputs/bronze_collect/channels_detail.jsonl.gz 안에서 채널을 검색합니다.
이 파일은 channels.list API 원본 응답을 그대로 모아둔 것이라 채널명, 채널ID,
구독자 수, 그리고 다른 곳엔 없는 채널 프로필 사진 URL까지 들어있습니다.

사용:
  python transforms/search_channel_avatar.py --title "치란"
      -> 채널명에 "치란"이 포함된 채널 검색 (대소문자 구분 안 함)

  python transforms/search_channel_avatar.py --id UCyn_E0ZE8RNOnYnF3lVaCOQ
      -> channel_id로 정확히 검색

  python transforms/search_channel_avatar.py --title "게임" --limit 30
      -> 검색 결과를 30개까지 표시 (기본 10개)
"""
import argparse
import gzip
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = f"{ROOT}/outputs/bronze_collect/channels_detail.jsonl.gz"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", help="채널명에 이 문자열이 포함된 채널 검색(대소문자 구분 안 함)")
    ap.add_argument("--id", help="channel_id로 정확히 검색")
    ap.add_argument("--limit", type=int, default=10, help="최대 표시 개수 (기본 10)")
    args = ap.parse_args()

    if not args.title and not args.id:
        ap.error("--title 또는 --id 중 하나는 필요합니다.")

    if not os.path.exists(PATH):
        print(f"파일이 없습니다: {PATH}")
        return

    seen = set()
    found = 0
    with gzip.open(PATH, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            for item in d.get("items", []):
                cid = item.get("id")
                snippet = item.get("snippet") or {}
                title = snippet.get("title", "")

                if args.id and cid != args.id:
                    continue
                if args.title and args.title.lower() not in title.lower():
                    continue
                if cid in seen:
                    continue
                seen.add(cid)

                thumbs = snippet.get("thumbnails") or {}
                avatar_url = (thumbs.get("medium") or thumbs.get("default") or {}).get("url")
                stats = item.get("statistics") or {}

                print(f"- {title}  (channel_id={cid})")
                print(f"    구독자: {stats.get('subscriberCount')}  프로필 사진: {avatar_url}")

                found += 1
                if found >= args.limit:
                    print(f"... 상위 {args.limit}개만 표시했습니다 (--limit 30 처럼 늘려서 더 볼 수 있어요)")
                    return

    if found == 0:
        print("검색 결과가 없습니다.")


if __name__ == "__main__":
    main()
