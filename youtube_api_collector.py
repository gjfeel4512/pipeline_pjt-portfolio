#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube 4개 카테고리(영화·애니메이션/자동차·차량/게임/인물·블로그) 공용 수집기
- 카테고리별 q 태그, 검색 구간(day)을 위에서 한눈에 관리
- 최근 1년을 카테고리별 구간(day) 단위로 순회하며 수집 (한 바퀴 아님)
- search() 결과가 나올 때마다 그 안의 video_id/channel_id 중 "아직 안 채운 것만"
  videos.list / channels.list로 즉시 보강
- 보강 직후 video_id 기준으로 search+videos.list+channels.list 응답을 그대로 합쳐서
  "bronze_merged.jsonl"에 한 줄씩 누적 저장 (+ 실행 후 bronze_merged.json으로도 변환 가능).
  이름에 silver를 안 붙인 이유: duration_seconds/video_type/KST 파생/is_valid 같은
  가공·검증이 아직 없어서 - 합치기만 한 것도 여전히 Bronze. 원본 낱개 응답도
  bronze_collect/에 별도 보존 (감사/재처리용)
- API 키 여러 개를 순서대로 쓰다가, 하나가 할당량 초과되면 자동으로 다음 키로 전환
- 이미 완료한 작업 / 이미 보강한 video_id·channel 정보는 체크포인트에 기록 -> 재실행 시 이어서 진행
"""

import googleapiclient.discovery
from googleapiclient.errors import HttpError
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Tuple
import json
import os
import time

# ============================================================
# 설정 (여기서 수정)
# ============================================================

# API 키 목록 - 할당량 초과되면 순서대로 다음 키로 넘어감.
# 추후 키 추가할 땐 아래 리스트에 그대로 이어서 넣으면 됨.
YOUTUBE_API_KEYS = [
    "AIzaSyAvGNG-VAU3Y_kItqH-ueeAf9t0aoqhWLQ",

    "AIzaSyDtM4DJYZMF9lVI-DvrUzz2Kz1CUvjwXVA",

    "AIzaSyBurzIteKFHZx84joOVX-li0ybcu-tOypA",

    "AIzaSyDmYWHl_bIMSOBEOhaFlPnWNSE4uJNKnEw",

    "AIzaSyB_vaQ3pKyVIlOAAJPtvKIc6YCEGoaGeqo",

    "AIzaSyDL86G0pK9MvX4n8-WtG5DOy8ee6S2w5z0",

    "AIzaSyD1qT-LyA_9XDffjFCULdXT8dXYRJx4C5k",

    "AIzaSyC0QrhkBAZRScV_K37mhBOv6LiOrJNwhOM",
]

# 카테고리 ID (YouTube 공식 videoCategoryId)
CATEGORY_IDS = {
    "영화_애니메이션": "1",
    "자동차_차량":     "2",
    "게임":            "20",
    "인물_블로그":      "22",
}

# 파일명에는 한글 대신 영어 슬러그 사용
CATEGORY_SLUGS = {
    "영화_애니메이션": "film_animation",
    "자동차_차량":     "autos_vehicles",
    "게임":            "gaming",
    "인물_블로그":      "people_blogs",
}
CATEGORY_ID_TO_LABEL = {v: k for k, v in CATEGORY_IDS.items()}

# 카테고리별 검색어(q) 태그 - 한눈에 비교/수정 가능하도록 나열
# 게임(20)은 "게임" 단독 포함(물량 우선), 인물_블로그(22)는 나머지 3종의 "리뷰 한정" 태그만 사용(순도 우선)
CATEGORY_TAGS = {
    "영화_애니메이션": ["영화리뷰", "영화 리뷰", "영화 후기", "영화 평론", "노스포 리뷰"],
    "자동차_차량":     ["자동차리뷰", "자동차 리뷰", "차량리뷰", "시승기", "신차 리뷰"],
    "게임":            ["게임", "게임리뷰", "게임 리뷰", "게임 후기"],
    "인물_블로그":      [
        "영화리뷰", "영화 리뷰", "영화 후기", "영화 평론", "노스포 리뷰",
        "자동차리뷰", "자동차 리뷰", "차량리뷰", "시승기", "신차 리뷰",
        "게임리뷰", "게임 리뷰", "게임 후기",
    ],
}

# 카테고리별 검색 구간 길이(일). 실측 밀도 기반으로 45~50개 근처가 되도록 조정한 값.
CATEGORY_WINDOW_DAYS = {
    "영화_애니메이션": 11,
    "자동차_차량":     12,
    "게임":            7,
    "인물_블로그":      20,
}

TOTAL_DAYS_BACK = 365                         # 얼마나 과거까지 수집할지
SEARCH_VIDEO_DURATIONS = ["medium", "long"]   # 쇼츠(0~4분 전체) 제외
REGION_CODE = "KR"
RELEVANCE_LANGUAGE = "ko"
MAX_RESULTS = 50                              # search API 최대치

OUTPUT_DIR = "outputs/bronze_collect"                    # Bronze 원본 JSON 저장 폴더 (호출 1번 = 파일 1개)
CHECKPOINT_FILE = "outputs/api_checkpoint.json"          # 진행 상황 기록 파일

# video_id 기준으로 search+videos.list+channels.list 응답을 그대로 합친 결과.
# "합쳤다"고 해서 Silver가 되는 게 아니다 - duration_seconds/video_type/KST 파생
# 시각/is_valid 같은 계산·검증이 아직 하나도 없으므로, 이건 여전히 Bronze다
# (API가 준 원본 필드를 재구성만 한 것 - 가공/검증된 게 아님).
#
# 파일 하나에 1년치(9천개 이상)를 다 이어쓰면 갈수록 무거워지고 카테고리별로
# 나눠서 다시 처리하기도 불편해서, 카테고리 x 연-월 단위로 쪼갠다.
# 예: outputs/bronze_merged/gaming_2026-08.jsonl
BRONZE_MERGED_DIR = "outputs/bronze_merged"

# 분당 호출 속도 제한(rateLimitExceeded, 보통 429)은 하루 할당량 소진(quotaExceeded)과
# 전혀 다른 문제라 - 몇 초~몇 분이면 풀리지 자정까지 기다릴 필요가 없다.
# 지수 백오프로 같은 키에 재시도하고, 그래도 계속 안 풀리면 그때 키를 전환한다.
RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BASE_DELAY_SEC = 2      # 재시도 대기시간: 2, 4, 8, 16, 32초로 증가
CALL_PACING_SEC = 0.3              # 매 API 호출 뒤 최소 간격 (애초에 속도 제한에 덜 걸리게)

# ============================================================
# 키 로테이션
# ============================================================
_current_key_index = 0
_youtube_client = None


def get_client():
    """현재 키로 만든 youtube client를 반환 (없으면 새로 생성)"""
    global _youtube_client
    if _youtube_client is None:
        if not YOUTUBE_API_KEYS:
            raise RuntimeError("YOUTUBE_API_KEYS가 비어 있습니다. 최소 1개는 넣어주세요.")
        key = YOUTUBE_API_KEYS[_current_key_index]
        _youtube_client = googleapiclient.discovery.build("youtube", "v3", developerKey=key)
    return _youtube_client


def rotate_to_next_key():
    """다음 키로 전환. 더 이상 남은 키가 없으면 False 반환"""
    global _current_key_index, _youtube_client
    _current_key_index += 1
    if _current_key_index >= len(YOUTUBE_API_KEYS):
        return False
    print(f"    🔑 키 {_current_key_index}번({_current_key_index+1}/{len(YOUTUBE_API_KEYS)})으로 전환")
    _youtube_client = None  # 다음 get_client() 호출 시 새 키로 재생성
    return True


def is_rate_limit_error(e):
    """분당 호출 속도 제한(보통 429, reason=rateLimitExceeded/userRateLimitExceeded).
    하루 할당량과 무관 - 몇 초~몇 분 대기 후 같은 키로 재시도하면 풀린다.
    (에러 메시지 텍스트에 "per day"가 섞여 나올 때도 있어서 헷갈리기 쉬운데,
    reason 코드가 rateLimitExceeded면 이쪽으로 분류해야 한다 - 실측으로 확인됨)"""
    if not isinstance(e, HttpError):
        return False
    if e.resp.status != 429:
        return False
    content = str(e.content)
    return "rateLimitExceeded" in content or "userRateLimitExceeded" in content


def is_daily_quota_error(e):
    """진짜 하루 할당량 소진 (reason=quotaExceeded/dailyLimitExceeded).
    이건 태평양 시간 자정 리셋까지 기다리
    거나 다른 키로 넘어가야 한다."""
    if not isinstance(e, HttpError):
        return False
    if e.resp.status not in (403, 429):
        return False
    content = str(e.content)
    return "quotaExceeded" in content or "dailyLimitExceeded" in content


def call_with_rotation(api_call_fn: Callable[[], Any]) -> Tuple[bool, Optional[Any]]:
    """api_call_fn(인자 없는 콜러블)을 실행.
    - 분당 속도 제한: 지수 백오프로 같은 키에 재시도 (최대 RATE_LIMIT_MAX_RETRIES회)
    - 하루 할당량 소진: 다음 키로 즉시 전환
    - 속도 제한 재시도를 다 써도 안 풀리면 다음 키로 전환 (그 키도 문제일 수 있으니)
    성공: (True, 결과) / 모든 키 소진: (False, None) / 그 외 에러: 그대로 raise
    (ok가 True일 때 결과는 항상 non-None - 호출부에서 `if not ok: return`으로 먼저 걸러낸 뒤 사용)"""
    rate_limit_retries = 0
    while True:
        try:
            result = api_call_fn()
            time.sleep(CALL_PACING_SEC)  # 다음 호출까지 최소 간격 확보 (속도 제한 예방)
            return True, result
        except Exception as e:
            if is_rate_limit_error(e) and not is_daily_quota_error(e):
                rate_limit_retries += 1
                if rate_limit_retries > RATE_LIMIT_MAX_RETRIES:
                    rate_limit_retries = 0
                    if rotate_to_next_key():
                        continue
                    return False, None
                delay = RATE_LIMIT_BASE_DELAY_SEC * (2 ** (rate_limit_retries - 1))
                print(f"    ⏳ 속도 제한 감지, {delay}초 대기 후 재시도 ({rate_limit_retries}/{RATE_LIMIT_MAX_RETRIES})")
                time.sleep(delay)
                continue
            if is_daily_quota_error(e):
                if rotate_to_next_key():
                    rate_limit_retries = 0
                    continue
                return False, None
            raise


# ============================================================
# 체크포인트 (진행 상황 저장/복원)
# - completed_search_tasks: 끝난 검색 작업 id 목록. 이게 전부다.
#
# video/channel 데이터는 체크포인트에 캐싱하지 않는다. videos.list/channels.list는
# 배치당 1unit이라(사실상 공짜) task마다 매번 다시 불러도 1년 전체로 따져도 몇백
# unit밖에 안 되는데, 캐싱하면 그 값을 통째로 저장해둬야 해서 체크포인트만 불필요하게
# 불어난다 (실제로 video_facts 캐싱 뺐을 때 440KB->28KB, channel_info까지 빼면
# completed_search_tasks 목록만 남아 훨씬 더 작아진다). 실제 데이터는 어차피
# bronze_collect/(낱개 원본)와 bronze_merged.jsonl(join된 결과)에 이미 다 남아있어서
# 체크포인트가 따로 들고 있을 필요가 없다.
# ============================================================
def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}
    return {
        "completed_search_tasks": set(data.get("completed_search_tasks", [])),
    }


def save_checkpoint(state):
    """체크포인트는 기계가 관리하는 상태 파일이라 예쁘게 들여쓸 필요가 없다."""
    os.makedirs(os.path.dirname(CHECKPOINT_FILE) or ".", exist_ok=True)
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "completed_search_tasks": sorted(state["completed_search_tasks"]),
        }, f, ensure_ascii=False, separators=(",", ":"))


def task_id(category_label, duration, period_index):
    """구간을 통째로 문자열(전체 ISO 타임스탬프)로 안 넣고 인덱스만 쓴다.
    generate_periods()가 window_days/total_days_back이 같으면 항상 같은 순서로
    같은 구간 리스트를 만들어내는 결정론적 함수라, "몇 번째 구간"만 알면 실제
    기간(period_start~period_end)은 언제든 다시 계산할 수 있다."""
    return f"{category_label}|{duration}|{period_index}"


# ============================================================
# 구간(기간) 생성 - "최근 N일" 한 번이 아니라 1년치를 구간별로 순회
# ============================================================
def generate_periods(window_days, total_days_back):
    """오늘부터 거슬러 올라가며 window_days 길이의 구간들을 생성 (겹치지 않음).

    기준 시점을 '내일 자정(00:00)'으로 고정한다 - datetime.now()를 그대로 쓰면
    같은 날 여러 번 실행해도 초 단위로 값이 달라져서 체크포인트가 같은 작업을
    다른 작업으로 착각해 중복 수집하는 버그가 생긴다. 자정 기준으로 고정하면
    같은 날 안에서는 몇 번을 실행해도 구간 경계가 항상 동일하다.
    """
    today = datetime.now().date()
    anchor = datetime.combine(today, datetime.min.time()) + timedelta(days=1)
    oldest = anchor - timedelta(days=total_days_back)
    periods = []
    cursor_end = anchor
    while cursor_end > oldest:
        cursor_start = max(cursor_end - timedelta(days=window_days), oldest)
        periods.append((cursor_start, cursor_end))
        cursor_end = cursor_start
    return periods


def build_all_tasks():
    """카테고리 x 길이 x 구간 전체 검색 작업 목록 생성"""
    tasks = []
    for label, cat_id in CATEGORY_IDS.items():
        tags = CATEGORY_TAGS[label]
        window_days = CATEGORY_WINDOW_DAYS[label]
        periods = generate_periods(window_days, TOTAL_DAYS_BACK)
        for period_index, (period_start, period_end) in enumerate(periods):
            for duration in SEARCH_VIDEO_DURATIONS:
                tasks.append({
                    "category_label": label,
                    "category_id": cat_id,
                    "tags": tags,
                    "duration": duration,
                    "period_index": period_index,
                    "period_start": period_start,
                    "period_end": period_end,
                })
    return tasks


# ============================================================
# 1단계: search() 호출 + 원본 저장
# ============================================================
def do_search(task):
    q = "|".join(f'"{t}"' for t in task["tags"])
    published_after = task["period_start"].isoformat("T") + "Z"
    published_before = task["period_end"].isoformat("T") + "Z"

    def call():
        youtube = get_client()
        return youtube.search().list(
            part="snippet",
            type="video",
            q=q,
            videoCategoryId=task["category_id"],
            videoDuration=task["duration"],
            order="date",
            publishedAfter=published_after,
            publishedBefore=published_before,
            regionCode=REGION_CODE,
            relevanceLanguage=RELEVANCE_LANGUAGE,
            maxResults=MAX_RESULTS,
        ).execute()

    ok, resp = call_with_rotation(call)
    if not ok:
        return False, None
    meta = {
        "q": q, "videoCategoryId": task["category_id"], "videoDuration": task["duration"],
        "publishedAfter": published_after, "publishedBefore": published_before,
        "regionCode": REGION_CODE, "relevanceLanguage": RELEVANCE_LANGUAGE,
        "maxResults": MAX_RESULTS, "order": "date",
    }
    return True, (resp, meta)


def save_search_result(task, resp, meta):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    start_tag = task["period_start"].strftime("%Y%m%d")
    end_tag = task["period_end"].strftime("%Y%m%d")
    fname = os.path.join(
        OUTPUT_DIR,
        f"{task['category_label']}_{task['duration']}_{start_tag}-{end_tag}.json",
    )
    with open(fname, "w", encoding="utf-8") as f:
        json.dump({"request_params": meta, "response": resp}, f, ensure_ascii=False, indent=2)
    return fname


# ============================================================
# 2단계: videos.list / channels.list 보강 (전역 중복 방지)
# ============================================================
def enrich_videos(video_ids):
    """새 video_id들만 videos.list로 보강. 성공 시 (True, [detail...]), 소진 시 (False, [])"""
    if not video_ids:
        return True, []
    details = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]

        def call(batch=batch):
            youtube = get_client()
            return youtube.videos().list(
                id=",".join(batch), part="snippet,contentDetails,statistics,status"
            ).execute()

        ok, resp = call_with_rotation(call)
        if not ok:
            return False, details  # 이미 성공한 배치는 details에 남아있음 (호출부에서 저장)
        details.extend(resp.get("items", []))
    return True, details


def enrich_channels(channel_ids):
    """새 channel_id들만 channels.list로 보강. 성공 시 (True, [detail...]), 소진 시 (False, [])"""
    if not channel_ids:
        return True, []
    details = []
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i + 50]

        def call(batch=batch):
            youtube = get_client()
            return youtube.channels().list(
                id=",".join(batch), part="snippet,statistics"
            ).execute()

        ok, resp = call_with_rotation(call)
        if not ok:
            return False, details
        details.extend(resp.get("items", []))
    return True, details


def save_enrichment(kind, items):
    """kind: 'videos' 또는 'channels'. 배치 하나를 파일 하나로 저장 (Bronze 원본 보존용)"""
    if not items:
        return None
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    fname = os.path.join(OUTPUT_DIR, f"_{kind}_detail_{suffix}.json")
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    return fname


# ============================================================
# 3단계: video_id 기준으로 search+videos.list+channels.list 응답을 그대로 합침
# (아직 Bronze임 - duration_seconds/video_type/KST 파생/is_valid 같은 가공·검증이
#  하나도 없음. 계산 없이 응답 필드를 재구성만 한 것)
# ============================================================
def extract_video_fact(v):
    """videos.list 응답 아이템 하나를 평탄화된 형태로 변환 (channel_id는 참조로 남겨둠,
    채널 통계는 아직 안 붙임 - join은 build_flat_records에서 channel_info와 합칠 때 함).
    응답에 있는 필드는 버리지 않고 전부 옮긴다 (전엔 liveBroadcastContent를 빠뜨렸었음)."""
    sn = v.get("snippet", {})
    cd = v.get("contentDetails", {})
    st = v.get("statistics", {})
    status = v.get("status", {})
    return {
        "video_id": v.get("id", ""),
        "category_id": sn.get("categoryId", ""),
        "title": sn.get("title", ""),
        "description": sn.get("description", ""),
        "published_at": sn.get("publishedAt", ""),
        "tags": sn.get("tags", []),
        "live_broadcast_content": sn.get("liveBroadcastContent", ""),
        "view_count": st.get("viewCount"),
        "like_count": st.get("likeCount"),
        "comment_count": st.get("commentCount"),
        "duration": cd.get("duration", ""),
        "definition": cd.get("definition", ""),
        "caption": cd.get("caption", ""),
        "privacy_status": status.get("privacyStatus", ""),
        "channel_id": sn.get("channelId", ""),
    }


def build_flat_records(video_facts, channel_info):
    """video_facts(평탄화된 영상 리스트) + channel_info를 channel_id 기준으로
    합쳐서 레코드로 만든다 (재처리 편하게 원래 타입 유지: tags는 리스트 그대로,
    숫자류는 API가 준 그대로). channel_published_at/hidden_subscriber_count도
    전에는 응답에 있는데 버렸던 필드라 여기 포함시킨다.
    collected_at은 UTC로 명시 (전에는 로컬시각을 썼는데 이름과 실제가 안 맞았음)."""
    records = []
    collected_at = datetime.now(timezone.utc).isoformat()
    for vf in video_facts:
        cid = vf.get("channel_id", "")
        ch = channel_info.get(cid, {})

        records.append({
            "category_name": CATEGORY_ID_TO_LABEL.get(vf.get("category_id", ""), ""),
            "category_id": vf.get("category_id", ""),
            "video_id": vf.get("video_id", ""),
            "title": vf.get("title", ""),
            "description": vf.get("description", ""),
            "published_at": vf.get("published_at", ""),
            "tags": vf.get("tags", []),
            "live_broadcast_content": vf.get("live_broadcast_content", ""),
            "view_count": vf.get("view_count"),
            "like_count": vf.get("like_count"),
            "comment_count": vf.get("comment_count"),
            "duration": vf.get("duration", ""),
            "definition": vf.get("definition", ""),
            "caption": vf.get("caption", ""),
            "privacy_status": vf.get("privacy_status", ""),
            "channel_id": cid,
            "channel_name": ch.get("title", ""),
            "channel_published_at": ch.get("channel_published_at", ""),
            "subscriber_count": ch.get("subscriber_count"),
            "hidden_subscriber_count": ch.get("hidden_subscriber_count"),
            "channel_total_view_count": ch.get("channel_view_count"),
            "channel_total_video_count": ch.get("channel_video_count"),
            "channel_thumbnail_url": ch.get("channel_thumbnail_url", ""),
            "collected_at_utc": collected_at,
        })
    return records


def bronze_merged_jsonl_path(category_label, period_start):
    """카테고리(영어 슬러그) x 연-월 단위로 파일을 쪼갠다.
    예: outputs/bronze_merged/gaming_2026-08.jsonl
    (구간이 월 경계를 걸치면 period_start 기준 월로 분류 - 하나의 규칙으로 고정)"""
    slug = CATEGORY_SLUGS[category_label]
    ym = period_start.strftime("%Y-%m")
    return os.path.join(BRONZE_MERGED_DIR, f"{slug}_{ym}.jsonl")


def append_to_bronze_merged(records, jsonl_path):
    """JSON Lines로 append (한 줄 = 영상 레코드 1개, CSV처럼 이어 쓰기 가능).
    이름에 silver를 안 붙인 이유: duration_seconds/video_type/KST 파생/is_valid
    같은 가공·검증이 아직 없어서 - 응답 필드를 합치기만 한 것도 여전히 Bronze."""
    if not records:
        return
    os.makedirs(os.path.dirname(jsonl_path) or ".", exist_ok=True)
    with open(jsonl_path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def list_bronze_merged_jsonl_files():
    if not os.path.isdir(BRONZE_MERGED_DIR):
        return []
    return sorted(
        os.path.join(BRONZE_MERGED_DIR, fn)
        for fn in os.listdir(BRONZE_MERGED_DIR)
        if fn.endswith(".jsonl")
    )


def count_bronze_merged_lines():
    """진행상황 출력용 - 체크포인트에 영상 개수를 안 들고 있으니 전체 파일에서 직접 센다"""
    total = 0
    for path in list_bronze_merged_jsonl_files():
        with open(path, encoding="utf-8") as f:
            total += sum(1 for _ in f)
    return total


def convert_bronze_merged_to_json_arrays():
    """카테고리x연월별 JSON Lines 파일들을 각각 읽어서, 같은 이름의 JSON 배열
    파일(.json)로도 저장한다. jsonl은 이어쓰기에 적합하고, 배열 형태는 통짜로
    한 번에 읽어야 하는 도구/사람이 보기엔 더 익숙해서 둘 다 남겨둔다."""
    jsonl_files = list_bronze_merged_jsonl_files()
    if not jsonl_files:
        return 0, 0
    total_records = 0
    for jsonl_path in jsonl_files:
        records = []
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        json_path = jsonl_path[:-len(".jsonl")] + ".json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        total_records += len(records)
    return len(jsonl_files), total_records


# ============================================================
# 메인 루프
# ============================================================
def stop_gracefully(state, completed_count, total_count):
    save_checkpoint(state)
    print()
    print("=" * 70)
    print("🛑 보유한 모든 키의 할당량을 소진했습니다.")
    print(f"   검색 작업 완료: {completed_count}/{total_count}개")
    print(f"   합쳐진(Bronze) 영상: {count_bronze_merged_lines()}개")
    print("   내일(또는 키 추가 후) 이 스크립트를 다시 실행하면 이어서 진행됩니다.")
    print("=" * 70)


def main():
    print("=" * 70)
    print("🎬🚗🎮📹 YouTube 4개 카테고리 공용 수집기 (검색 + Bronze join + 키 로테이션)")
    print("=" * 70)

    all_tasks = build_all_tasks()
    state = load_checkpoint()
    remaining = [
        t for t in all_tasks
        if task_id(t["category_label"], t["duration"], t["period_index"])
        not in state["completed_search_tasks"]
    ]

    print(f"검색 작업: 전체 {len(all_tasks)}개 / 완료 {len(state['completed_search_tasks'])}개 / 남음 {len(remaining)}개")
    print(f"합쳐진(Bronze) 영상: {count_bronze_merged_lines()}개")
    print(f"보유 키 수: {len(YOUTUBE_API_KEYS)}개")
    print()

    if not remaining:
        print("✅ 모든 검색 작업이 이미 완료되어 있습니다.")
        nfiles, nrecords = convert_bronze_merged_to_json_arrays()
        if nfiles:
            print(f"   .json 배열로도 변환 저장: 파일 {nfiles}개, 총 {nrecords}건 -> {BRONZE_MERGED_DIR}/")
        return

    for i, task in enumerate(remaining, 1):
        tid = task_id(task["category_label"], task["duration"], task["period_index"])
        label = f"[{task['category_label']}/{task['duration']}] {task['period_start'].date()}~{task['period_end'].date()}"

        # 1) 검색
        try:
            ok, result = do_search(task)
        except Exception as e:
            print(f"({i}/{len(remaining)}) {label} -> ❌ 실패(할당량 아님): {e}")
            continue
        if not ok:
            stop_gracefully(state, len(state["completed_search_tasks"]), len(all_tasks))
            return

        resp, meta = result
        fname = save_search_result(task, resp, meta)
        items = resp.get("items", [])
        print(f"({i}/{len(remaining)}) {label} -> {len(items)}개 저장: {fname}")

        # 2) 영상 보강 (이 task의 영상 전부 - 캐싱 안 함. 영상은 카테고리+기간으로 이미
        #    안 겹치게 나눴으니 구조적으로 task 하나에만 등장해서, 전역 캐시로 아낄 비용이
        #    거의 없다. 재시도해도 그냥 다시 videos.list 하면 됨(1unit, 사실상 공짜).)
        video_ids = [it["id"]["videoId"] for it in items]
        try:
            ok, video_details = enrich_videos(video_ids)
        except Exception as e:
            print(f"    ⚠️ videos.list 실패(할당량 아님): {e}")
            ok, video_details = True, []
        if video_details:
            vf = save_enrichment("videos", video_details)
            print(f"    ㄴ 영상 보강 {len(video_details)}개 저장: {vf}")
        if not ok:
            # task를 완료로 표시하면 안 됨 - 이 task의 영상들이 아직 join/저장 안 됐는데
            # 완료로 찍히면 다음 실행 때 재시도가 안 돼서 bronze_merged.jsonl에 영영 안 들어감.
            # (search 자체는 성공했지만 Bronze에만 남고, task는 "미완료"로 남겨서
            #  다음 실행 때 검색부터 다시 하도록 한다 - 약간의 재검색 비용은 감수)
            save_checkpoint(state)
            stop_gracefully(state, len(state["completed_search_tasks"]), len(all_tasks))
            return

        task_video_facts = [extract_video_fact(v) for v in video_details]

        # 3) 채널 보강 (이 task의 영상들이 참조하는 channel_id 전부 - 이것도 캐싱 안 함.
        #    channels.list도 배치당 1unit이라, 같은 채널이 다른 task에 또 나와도
        #    다시 불러도 되고 그게 훨씬 단순하다. 캐싱해서 아낄 비용 자체가 미미함.)
        referenced_channel_ids = list({vf["channel_id"] for vf in task_video_facts})
        try:
            ok, channel_details = enrich_channels(referenced_channel_ids)
        except Exception as e:
            print(f"    ⚠️ channels.list 실패(할당량 아님): {e}")
            ok, channel_details = True, []
        channel_info_local = {}
        if channel_details:
            cf = save_enrichment("channels", channel_details)
            for c in channel_details:
                stats = c.get("statistics", {})
                sn = c.get("snippet", {})
                # thumbnails는 part=snippet 응답에 이미 포함되어 있던 걸 그동안 버리고
                # 있었음 - 추가 API 비용 없이 채널 프로필 이미지로 씀
                ch_thumbs = sn.get("thumbnails", {})
                ch_thumbnail_url = (
                    ch_thumbs.get("high", {}).get("url")
                    or ch_thumbs.get("medium", {}).get("url")
                    or ch_thumbs.get("default", {}).get("url")
                    or ""
                )
                channel_info_local[c["id"]] = {
                    "title": sn.get("title", ""),
                    "channel_published_at": sn.get("publishedAt", ""),
                    "subscriber_count": stats.get("subscriberCount", ""),
                    "hidden_subscriber_count": stats.get("hiddenSubscriberCount", ""),
                    "channel_view_count": stats.get("viewCount", ""),
                    "channel_video_count": stats.get("videoCount", ""),
                    "channel_thumbnail_url": ch_thumbnail_url,
                }
            print(f"    ㄴ 채널 보강 {len(channel_details)}개 저장: {cf}")
        if not ok:
            # 채널 정보가 일부만 채워진 상태로 join해서 남기면, 다음 실행 때 이 task를
            # 재시도할 때 같은 영상이 중복으로 또 append될 수 있다. 그래서 여기서도
            # task를 완료로 표시하지 않고, 부분 join 결과도 쓰지 않는다.
            save_checkpoint(state)
            stop_gracefully(state, len(state["completed_search_tasks"]), len(all_tasks))
            return

        # 4) channel_id 기준 join -> 카테고리x연월 jsonl에 즉시 append (여전히 Bronze)
        records = build_flat_records(task_video_facts, channel_info_local)
        if records:
            jsonl_path = bronze_merged_jsonl_path(task["category_label"], task["period_start"])
            append_to_bronze_merged(records, jsonl_path)
            print(f"    ㄴ {jsonl_path}에 {len(records)}건 추가")

        # 이 task 전체(검색+보강+join) 완료
        state["completed_search_tasks"].add(tid)
        save_checkpoint(state)  # 매 task 성공마다 즉시 기록 (중단돼도 유실 없음)

    print()
    print("=" * 70)
    print(f"완료: 검색 {len(state['completed_search_tasks'])}/{len(all_tasks)}개, "
          f"영상 {count_bronze_merged_lines()}개")
    nfiles, nrecords = convert_bronze_merged_to_json_arrays()
    if nfiles:
        print(f".json 배열로도 변환 저장: 파일 {nfiles}개, 총 {nrecords}건 -> {BRONZE_MERGED_DIR}/")
    print("=" * 70)


if __name__ == "__main__":
    main()
