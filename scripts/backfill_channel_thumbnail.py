#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
기존 로컬 백필 Bronze 파일(outputs/bronze_merged/*.jsonl)에는 channel_thumbnail_url이
없다 - youtube_api_collector.py가 이 필드를 추출하도록 고쳐지기 전에 수집된 데이터라서.
이 스크립트는 한 번만 실행하는 소급 보정용 (1회성):
  1. 전체 jsonl 파일에서 고유 channel_id를 모은다
  2. channels.list(part=snippet)로 각 채널의 썸네일 URL을 가져온다 (50개씩 배치)
  3. 각 jsonl 파일을 다시 읽어서 channel_thumbnail_url 필드를 채워 넣고 같은 파일에 덮어쓴다
  4. bronze_merged.json(배열 버전) 미러도 다시 생성한다

실행 후 파일 mtime이 바뀌므로, Airflow DAG(bronze_to_silver_dag_aws.py)의 체크포인트가
다음 실행 때 이 파일들을 "변경됨"으로 인식해서 자동으로 S3 Bronze/Silver로 재전송한다.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from youtube_api_collector import (  # noqa: E402
    enrich_channels,
    list_bronze_merged_jsonl_files,
    convert_bronze_merged_to_json_arrays,
)


def extract_thumbnail(channel_item):
    thumbs = channel_item.get("snippet", {}).get("thumbnails", {})
    return (
        thumbs.get("high", {}).get("url")
        or thumbs.get("medium", {}).get("url")
        or thumbs.get("default", {}).get("url")
        or ""
    )


def main():
    jsonl_files = list_bronze_merged_jsonl_files()
    if not jsonl_files:
        print("outputs/bronze_merged/*.jsonl 파일이 없습니다.")
        return

    print(f"대상 파일: {len(jsonl_files)}개")

    # 1) 고유 channel_id 수집
    channel_ids = set()
    for fp in jsonl_files:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                cid = rec.get("channel_id")
                if cid:
                    channel_ids.add(cid)
    channel_ids = sorted(channel_ids)
    n_calls = (len(channel_ids) + 49) // 50
    print(f"고유 channel_id: {len(channel_ids)}개 (channels.list 약 {n_calls}회 호출 예정)")

    # 2) channels.list로 썸네일 조회
    ok, details = enrich_channels(channel_ids)
    if not ok:
        print("경고: API 키를 전부 소진해서 일부만 조회했을 수 있습니다. 조회된 만큼만 반영합니다.")
    thumb_map = {}
    for item in details:
        cid = item.get("id")
        if cid:
            thumb_map[cid] = extract_thumbnail(item)
    print(f"썸네일 확보: {len(thumb_map)}/{len(channel_ids)}개 채널")

    missing = [c for c in channel_ids if c not in thumb_map]
    if missing:
        print(f"썸네일을 못 가져온 채널 {len(missing)}개(탈퇴/비공개 등 가능) - channel_thumbnail_url은 빈 문자열로 채움")

    # 3) 각 jsonl 파일 갱신
    total_updated = 0
    total_records = 0
    for fp in jsonl_files:
        with open(fp, encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        records = [json.loads(line) for line in lines]
        changed = False
        for rec in records:
            cid = rec.get("channel_id")
            new_thumb = thumb_map.get(cid, "")
            if rec.get("channel_thumbnail_url") != new_thumb:
                rec["channel_thumbnail_url"] = new_thumb
                changed = True
        total_records += len(records)
        if changed:
            with open(fp, "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            total_updated += 1
            print(f"  갱신: {os.path.basename(fp)} ({len(records)}건)")

    print(f"\n총 {total_updated}/{len(jsonl_files)}개 파일 갱신, {total_records}건 레코드 처리")

    # 4) .json 배열 미러 재생성
    n_files, n_records = convert_bronze_merged_to_json_arrays()
    print(f".json 미러 재생성 완료: {n_files}개 파일, {n_records}건")


if __name__ == "__main__":
    main()
