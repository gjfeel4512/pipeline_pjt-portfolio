# transforms/push_to_firehose.py
"""
Bronze JSONL 파일을 Kinesis Firehose로 전송 -> S3에 자동 파티셔닝 적재
(Airflow DAG에도 동일 로직이 내장되어 있음: dags/bronze_to_silver_dag_aws.py 참고.
 이 스크립트는 로컬에서 수동으로 빠르게 테스트하고 싶을 때 사용)
"""
import json
import os
from datetime import datetime, timedelta, timezone
import boto3
from pathlib import Path

KST = timezone(timedelta(hours=9))

AWS_REGION = os.getenv("AWS_DEFAULT_REGION", "us-west-2")
FIREHOSE_STREAM = os.getenv("FIREHOSE_STREAM_NAME", "goldline-dev-bronze-stream")

firehose = boto3.client("firehose", region_name=AWS_REGION)

# YouTube 카테고리 ID -> 영문 슬러그 매핑 (실제 Bronze 데이터의 category_id 필드 기준)
CATEGORY_ID_MAP = {
    "1": "film_animation",   # 영화_애니메이션
    "2": "autos_vehicles",   # 자동차_차량
    "20": "gaming",          # 게임
    "22": "people_blogs",    # 인물_블로그
}


def push_jsonl_to_firehose(file_path: Path):
    records = []
    sent = 0

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            cat_id = str(row.get("category_id", ""))
            row["category"] = CATEGORY_ID_MAP.get(cat_id, "unknown")
            # Firehose S3 prefix의 year=/month=/day=는 !{timestamp:...}를 쓰면 항상
            # UTC 기준이라 KST와 어긋난다 - 레코드에 KST 날짜를 직접 실어서
            # !{partitionKeyFromQuery:...}로 대체한다 (infra/firehose.tf 참고)
            try:
                collected_kst = datetime.fromisoformat(
                    row.get("collected_at_utc", "").replace("Z", "+00:00")
                ).astimezone(KST)
            except (ValueError, AttributeError):
                collected_kst = datetime.now(KST)
            row["year_kst"] = collected_kst.strftime("%Y")
            row["month_kst"] = collected_kst.strftime("%m")
            row["day_kst"] = collected_kst.strftime("%d")
            records.append({"Data": (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")})

            if len(records) == 500:  # Firehose batch 최대 500개
                firehose.put_record_batch(DeliveryStreamName=FIREHOSE_STREAM, Records=records)
                sent += len(records)
                records = []

    if records:
        firehose.put_record_batch(DeliveryStreamName=FIREHOSE_STREAM, Records=records)
        sent += len(records)

    print(f"{file_path.name} -> Firehose 전송 완료 ({sent}건)")
    return sent


if __name__ == "__main__":
    bronze_dir = Path("outputs/bronze_merged")
    total = 0
    for jsonl_file in sorted(bronze_dir.glob("*.jsonl")):
        total += push_jsonl_to_firehose(jsonl_file)
    print(f"\n총 {total}건 전송 완료")
