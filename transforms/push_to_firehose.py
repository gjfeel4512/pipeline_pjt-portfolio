# transforms/push_to_firehose.py
import json
import boto3
from pathlib import Path

FIREHOSE_STREAM = "pipeline-pjt-dev-bronze-stream"  # terraform output 값
firehose = boto3.client("firehose", region_name="us-west-2")

CATEGORY_MAP = {
    "film_animation": "film_animation",
    "autos_vehicles": "autos_vehicles",
    "gaming": "gaming",
    "people_blogs": "people_blogs",
}

def push_jsonl_to_firehose(file_path: Path, category: str):
    records = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            row["category"] = category   # 파티션 키 주입
            records.append({"Data": (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")})

            if len(records) == 500:  # Firehose batch 최대 500개
                firehose.put_record_batch(DeliveryStreamName=FIREHOSE_STREAM, Records=records)
                records = []

    if records:
        firehose.put_record_batch(DeliveryStreamName=FIREHOSE_STREAM, Records=records)

    print(f"✅ {file_path.name} → Firehose 전송 완료 (category={category})")


if __name__ == "__main__":
    bronze_dir = Path("outputs/bronze_merged")
    for jsonl_file in bronze_dir.glob("*.jsonl"):
        for key in CATEGORY_MAP:
            if key in jsonl_file.name:
                push_jsonl_to_firehose(jsonl_file, CATEGORY_MAP[key])
                break