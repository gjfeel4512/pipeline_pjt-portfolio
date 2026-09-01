#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
더미 YouTube 데이터 생성 스크립트
용도: Bronze 레이어 테스트 데이터 생성
생성 위치: outputs/bronze_merged/youtube_dummy_data.jsonl
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
import uuid
import argparse

# 생성 설정
OUTPUT_DIR = Path("outputs/bronze_merged")
NUM_RECORDS_DEFAULT = 1000

# 더미 데이터용 샘플
CHANNELS = [
    "Tech Daily", "Coding Tutorials", "Music Channel", "Food Review", "Gaming",
    "Nature Documentary", "Travel Vlog", "Comedy", "Education", "Sports"
]

COUNTRIES = ["US", "KR", "JP", "CN", "GB", "DE", "FR", "CA", "AU", "BR", "IN", "MX"]

DEVICES = ["mobile", "desktop", "tablet"]

EVENT_TYPES = [
    "video_view", "video_search", "playlist_created",
    "video_uploaded", "comment_added", "like_clicked",
    "subscribe", "watch_later"
]

PLATFORMS = ["iOS", "Android", "Web", "Smart TV"]

LANGUAGES = ["en", "ko", "ja", "zh", "es", "fr", "de", "pt", "ru"]


def generate_dummy_record(index, days_offset=30):
    """단일 더미 레코드 생성"""

    # 랜덤 타임스탬프 (지난 N일)
    days_ago = random.randint(0, days_offset)
    hours_ago = random.randint(0, 23)
    minutes_ago = random.randint(0, 59)

    occurred_at = (
        datetime.utcnow() - timedelta(
            days=days_ago,
            hours=hours_ago,
            minutes=minutes_ago
        )
    )

    # 조회수 기반 통계 (로그 정규분포)
    view_count = int(10 ** random.uniform(2, 5.5))  # 100 ~ 300,000
    like_count = int(view_count * random.uniform(0.01, 0.15))
    comment_count = int(view_count * random.uniform(0.001, 0.08))

    # 동영상 길이 (초)
    duration_seconds = random.randint(60, 7200)  # 1분 ~ 2시간

    # 상태 코드 (대부분 성공)
    status_code = random.choices(
        [200, 201, 400, 404, 429, 500],
        weights=[800, 100, 50, 20, 20, 10]
    )[0]

    record = {
        "_id": str(uuid.uuid4()),
        "schema_version": "1.0",
        "record_type": "application_log",
        "event_id": str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "run_id": f"run_202609_{index:06d}",

        # 타임스탬프
        "occurred_at": occurred_at.isoformat() + "+09:00",
        "generated_at_utc": datetime.utcnow().isoformat() + "+00:00",

        # 도메인
        "domain": "youtube",
        "event_type": random.choice(EVENT_TYPES),

        # 서비스 정보
        "service": {
            "name": "YouTube API",
            "version": "3.0",
            "region": "us-west-2",
            "environment": "production"
        },

        # 클라이언트 정보
        "client": {
            "country": random.choice(COUNTRIES),
            "platform": random.choice(PLATFORMS),
            "device_type": random.choice(DEVICES),
            "app_version": f"v{random.randint(1, 8)}.{random.randint(0, 9)}.{random.randint(0, 99)}"
        },

        # 요청 정보
        "request": {
            "method": random.choice(["GET", "POST", "PUT"]),
            "path": random.choice([
                "/api/youtube/videos",
                "/api/youtube/search",
                "/api/youtube/channels",
                "/api/youtube/playlists"
            ]),
            "request_bytes": random.randint(100, 5000)
        },

        # 응답 정보
        "response": {
            "status_code": status_code,
            "latency_ms": random.randint(50, 5000),
            "response_bytes": random.randint(1000, 100000)
        },

        # 비디오 데이터
        "data": {
            "video_id": f"vid_{index:08d}",
            "title": f"Sample Video {index}: {random.choice(['Tutorial', 'Review', 'Gaming', 'Music', 'Vlog', 'Documentary', 'Comedy'])}",
            "description": f"This is a sample video for testing. Video ID: {index}",
            "channel_name": random.choice(CHANNELS),
            "channel_id": f"ch_{random.randint(100000, 999999)}",

            # 메타데이터
            "duration": f"PT{duration_seconds // 60}M{duration_seconds % 60}S",
            "published_at": (
                occurred_at - timedelta(days=random.randint(1, 365))
            ).isoformat(),

            # 통계
            "view_count": view_count,
            "like_count": like_count,
            "comment_count": comment_count,
            "share_count": int(view_count * random.uniform(0.001, 0.02)),
            "favorite_count": int(view_count * random.uniform(0.0001, 0.01)),
            "subscriber_count": random.randint(100, 10000000),

            # 추가 정보
            "is_monetized": random.choice([True, False, False, False]),
            "is_live": random.choice([True, False, False, False]),
            "language": random.choice(LANGUAGES),
            "category": random.choice([
                "Entertainment", "Music", "Gaming", "Education",
                "Science", "Sports", "Travel", "Food", "Tech", "News"
            ]),
            "tags": [f"tag_{i}" for i in range(random.randint(3, 15))],

            # 품질 지표
            "engagement_rate": (like_count + comment_count) / view_count if view_count > 0 else 0,
            "upload_date": occurred_at.date().isoformat()
        }
    }

    return record


def generate_dummy_data(num_records=NUM_RECORDS_DEFAULT, output_name="youtube_dummy_data"):
    """더미 데이터 파일 생성"""

    output_file = OUTPUT_DIR / f"{output_name}.jsonl"

    print("=" * 60)
    print("📊 YouTube 더미 데이터 생성")
    print("=" * 60)
    print(f"📝 생성할 레코드: {num_records:,}개")
    print(f"💾 출력 위치: {output_file}")

    # 출력 폴더 생성
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 데이터 생성 및 저장
    start_time = datetime.now()

    with open(output_file, 'w', encoding='utf-8') as f:
        for i in range(num_records):
            record = generate_dummy_record(i)
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

            # 진행 상황 표시
            if (i + 1) % max(1, num_records // 10) == 0 or (i + 1) % 1000 == 0:
                percentage = ((i + 1) / num_records) * 100
                print(f"  ✓ {i + 1:,}/{num_records:,} ({percentage:.1f}%)")

    # 파일 통계
    elapsed_time = (datetime.now() - start_time).total_seconds()
    file_size = output_file.stat().st_size / (1024 * 1024)  # MB
    records_per_second = num_records / elapsed_time if elapsed_time > 0 else 0

    print("\n" + "=" * 60)
    print("✅ 데이터 생성 완료!")
    print("=" * 60)
    print(f"📊 통계:")
    print(f"   - 파일명: {output_file.name}")
    print(f"   - 레코드: {num_records:,}개")
    print(f"   - 파일크기: {file_size:.2f} MB")
    print(f"   - 생성시간: {elapsed_time:.2f}초")
    print(f"   - 속도: {records_per_second:.0f} records/sec")
    print(f"\n📂 다음 단계:")
    print(f"   1. AWS S3에 업로드:")
    print(f"      aws s3 sync outputs/bronze_merged/ s3://goldline-dev-bronze-[계정ID]/ --region us-west-2")
    print(f"   2. Airflow DAG 실행: bronze_to_silver_with_s3")
    print(f"   3. Silver 데이터 확인: s3://goldline-dev-silver-[계정ID]/")
    print("=" * 60)


def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(
        description='YouTube 더미 데이터 생성',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예제:
  python generate_dummy_bronze_data.py                    # 기본값 (1000개)
  python generate_dummy_bronze_data.py --count 5000       # 5000개 생성
  python generate_dummy_bronze_data.py --count 100 --name test  # test 데이터
        """
    )

    parser.add_argument(
        '--count', '-c',
        type=int,
        default=NUM_RECORDS_DEFAULT,
        help=f'생성할 레코드 수 (기본값: {NUM_RECORDS_DEFAULT})'
    )

    parser.add_argument(
        '--name', '-n',
        type=str,
        default='youtube_dummy_data',
        help='출력 파일명 (기본값: youtube_dummy_data)'
    )

    args = parser.parse_args()

    try:
        generate_dummy_data(args.count, args.name)
    except KeyboardInterrupt:
        print("\n\n❌ 작업 취소됨")
    except Exception as e:
        print(f"\n\n❌ 오류 발생: {e}")
        raise


if __name__ == "__main__":
    main()
