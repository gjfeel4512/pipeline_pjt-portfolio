#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube search.list 기반 일일 수집 Lambda
- videos.list(chart=mostPopular) 대신 search.list로 수집 -> 이미 뜬 영상만 모이는
  survivorship bias가 없음 (신규/소형 채널 영상도 검색어·기간 매칭이면 잡힘)
- 카테고리(영화·애니메이션/자동차·차량/게임/인물·블로그)별 검색어(q) 태그로
  search() -> 결과의 video_id/channel_id를 videos.list/channels.list로 보강
  -> video_id 기준으로 합쳐서 S3 Bronze에 JSON Lines로 저장
- Lambda 최대 실행시간(15분) 안에 전체 작업을 못 끝내면, 남은 시간이
  TIME_BUDGET_SAFETY_SEC 밑으로 떨어지는 시점에 지금까지 처리한 만큼만 S3에 올리고
  체크포인트(S3)를 저장한 뒤 종료한다. EventBridge가 다음 스케줄에 다시 호출하면
  체크포인트를 읽어 이어서 처리한다 (task 단위로 완료 여부를 기록하므로 재시도해도
  중복 처리되지 않음 - 이미 완료한 task는 건너뜀).
- 체크포인트는 실행일(run_date, KST 날짜) 기준 파일이라 날짜가 바뀌면 그날의
  최근 TOTAL_DAYS_BACK일 구간으로 새로 시작한다. 같은 영상이 다음날 다시 수집되는
  것은 의도된 동작이다 (조회수 시계열 추적이 이 프로젝트의 핵심 - spec.md 참고).
- 표준 라이브러리(urllib)만 사용 -> googleapiclient 없이 Lambda Layer 불필요.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_BASE = "https://www.googleapis.com/youtube/v3"

# 파티션 키(bronze/search/.../year=/month=/day=)와 run_date/체크포인트 기준: KST(한국시간)
KST = timezone(timedelta(hours=9))

YOUTUBE_API_KEYS = [k.strip() for k in os.environ.get("YOUTUBE_API_KEYS", "").split(",") if k.strip()]
BUCKET_NAME = os.environ["BUCKET_NAME"]
REGION_CODE = os.environ.get("REGION_CODE", "KR")
RELEVANCE_LANGUAGE = os.environ.get("RELEVANCE_LANGUAGE", "ko")
MAX_RESULTS = int(os.environ.get("MAX_RESULTS", "50"))
TOTAL_DAYS_BACK = int(os.environ.get("TOTAL_DAYS_BACK", "7"))
# 남은 실행시간이 이 값(초) 밑으로 떨어지면 새 task를 시작하지 않고 지금까지 결과를 저장 후 종료
TIME_BUDGET_SAFETY_SEC = int(os.environ.get("TIME_BUDGET_SAFETY_SEC", "60"))

SEARCH_VIDEO_DURATIONS = ["medium", "long"]  # 쇼츠(0~4분 전체) 제외

# 카테고리 ID (YouTube 공식 videoCategoryId)
CATEGORY_IDS = {
    "영화_애니메이션": "1",
    "자동차_차량":     "2",
    "게임":            "20",
    "인물_블로그":      "22",
}
CATEGORY_SLUGS = {
    "영화_애니메이션": "film_animation",
    "자동차_차량":     "autos_vehicles",
    "게임":            "gaming",
    "인물_블로그":      "people_blogs",
}
CATEGORY_ID_TO_LABEL = {v: k for k, v in CATEGORY_IDS.items()}

# 카테고리별 검색어(q) 태그. 태그를 "번들"(동의어 묶음) 단위로 관리 - 번들 안의 태그는
# q="a"|"b"|"c" 형태의 OR 검색 1회로 합쳐서 보낸다(do_search 참고). 서로 다른 번들끼리는
# 여전히 개별 검색.
# 묶는 기준(2026-09-02 쿼터 절감 분석): (1) 사실상 동의어라 matched_tags 구분이
# 분석적으로 무의미 (2) 묶은 결과가 (기간×길이) 슬롯당 search API maxResults 캡(50)에
# 안 걸림(실측 co-occurrence 기반). 캡에 걸릴 위험이 있는 태그(예: 영화 "결말포함"/
# "영화리뷰", 게임 "신작 게임"/"게임 추천")는 개별 번들로 남겨둠.
# 실측 검증(2026-09-01) 기반 - 4개 카테고리 검색 결과 video_id 교차 중복 없음 확인.
CATEGORY_TAGS = {
    "영화_애니메이션": [
        ["영화리뷰"],
        ["결말포함"],
        # "해석/요약" 계열 동의어 - 합쳐도 슬롯당 ~8건으로 캡에 여유 충분
        ["영화 해석", "영화 요약", "영화 비평", "개봉작 리뷰", "영화 몰아보기"],
        ["영화 추천"],
        ["박스오피스"],
    ],
    "자동차_차량": [
        ["시승기"],
        # 신차/전기차/비교류 - 합쳐도 슬롯당 ~15건
        ["신차 리뷰", "전기차 리뷰", "차량 비교", "장기렌트 비교"],
        # 자동차 리뷰+중고차 리뷰 - 합쳐도 슬롯당 ~16건 (신차 급증 주에도 캡까지 여유 큼)
        ["자동차 리뷰", "중고차 리뷰"],
        ["차박"],
    ],
    "게임": [
        # 띄어쓰기만 다른 동일어 - 합쳐도 슬롯당 ~17건
        ["게임리뷰", "게임 리뷰", "게임 후기"],
        ["게임 공략", "게임 업데이트"],
        ["게임 추천"],   # 캡 위험(슬롯당 ~31건) - 개별 유지
        ["신작 게임"],   # 캡 위험(슬롯당 ~40건) - 개별 유지
        # 둘 다 "할인/무료" 인텐트, 리뷰류와 겹침 적음
        ["스팀 할인", "무료 게임"],
    ],
    # 진짜 브이로그 추적이 아니라, 다른 카테고리로 오분류된 리뷰어를 잡아내는 용도로
    # 최소화 - 태그 구분 자체가 원래 무의미해서 통째로 한 번들.
    # (category_id=22는 업로더가 카테고리를 지정 안 했을 때 YouTube가 자동으로 붙이는
    #  기본값이라 Silver 단계에서 오염 데이터로 분리됨 - 정상 3개 카테고리와 다른 취급)
    "인물_블로그": [["영화리뷰", "시승기", "게임리뷰"]],
}

# 카테고리별 검색 구간 길이(일). 실측 밀도 기반으로 태그당 검색 결과가 API 캡(50)에
# 안 걸리게 조정한 값.
CATEGORY_WINDOW_DAYS = {
    "영화_애니메이션":   4,
    "자동차_차량":      7,
    "게임":            4,
    "인물_블로그":      7,
}

RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BASE_DELAY_SEC = 2      # 재시도 대기시간: 2, 4, 8, 16, 32초로 증가
CALL_PACING_SEC = 1.0              # 매 API 호출 뒤 최소 간격 (분당 속도 제한 예방)

s3_client = None


def get_s3_client():
    global s3_client
    if s3_client is None:
        import boto3
        s3_client = boto3.client("s3")
    return s3_client


# ============================================================
# 키 로테이션 + 속도제한/할당량 구분
# ============================================================
class QuotaExhaustedError(Exception):
    """보유한 모든 API 키의 하루 할당량이 소진됨"""


class _ApiHttpError(Exception):
    def __init__(self, code, body):
        super().__init__(f"{code}: {body[:300]}")
        self.code = code
        self.body = body

    def is_rate_limit(self):
        """분당 호출 속도 제한(reason=rateLimitExceeded/userRateLimitExceeded).
        하루 할당량과 무관 - 몇 초~몇 분 대기 후 같은 키로 재시도하면 풀린다."""
        return self.code == 429 and (
            "rateLimitExceeded" in self.body or "userRateLimitExceeded" in self.body
        )

    def is_quota_exceeded(self):
        """진짜 하루 할당량 소진(reason=quotaExceeded/dailyLimitExceeded) -> 다음 키로 전환."""
        return self.code in (403, 429) and (
            "quotaExceeded" in self.body or "dailyLimitExceeded" in self.body
        )


def _api_get(path, params, key):
    query = dict(params)
    query["key"] = key
    url = f"{API_BASE}/{path}?{urllib.parse.urlencode(query)}"
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise _ApiHttpError(e.code, body) from None


_current_key_index = 0


def call_with_rotation(path, params):
    """분당 속도 제한이면 지수 백오프로 같은 키 재시도, 하루 할당량 소진이면 다음 키로 전환.
    성공 시 응답 dict 반환. 모든 키 소진 시 QuotaExhaustedError."""
    global _current_key_index
    while _current_key_index < len(YOUTUBE_API_KEYS):
        key = YOUTUBE_API_KEYS[_current_key_index]
        rate_limit_retries = 0
        while True:
            try:
                data = _api_get(path, params, key)
                time.sleep(CALL_PACING_SEC)
                return data
            except _ApiHttpError as e:
                if e.is_rate_limit() and not e.is_quota_exceeded():
                    rate_limit_retries += 1
                    if rate_limit_retries > RATE_LIMIT_MAX_RETRIES:
                        break  # 이 키는 포기하고 다음 키로 전환
                    delay = RATE_LIMIT_BASE_DELAY_SEC * (2 ** (rate_limit_retries - 1))
                    print(f"    속도 제한 감지, {delay}초 대기 후 재시도 ({rate_limit_retries}/{RATE_LIMIT_MAX_RETRIES})")
                    time.sleep(delay)
                    continue
                if e.is_quota_exceeded():
                    break  # 다음 키로 전환
                raise RuntimeError(f"YouTube API 오류: {e}")
        _current_key_index += 1
        if _current_key_index < len(YOUTUBE_API_KEYS):
            print(f"    키 {_current_key_index}번({_current_key_index + 1}/{len(YOUTUBE_API_KEYS)})으로 전환")
    raise QuotaExhaustedError("모든 API 키의 할당량이 소진되었습니다.")


# ============================================================
# 시간 예산 (Lambda 15분 하드 타임아웃 대응)
# ============================================================
def time_running_low(context):
    """context가 없으면(로컬 실행) 시간 제한 없음 취급."""
    if context is None or not hasattr(context, "get_remaining_time_in_millis"):
        return False
    return context.get_remaining_time_in_millis() < TIME_BUDGET_SAFETY_SEC * 1000


# ============================================================
# 체크포인트 (S3) - completed_search_tasks(끝난 검색 작업 id 집합)만 기록
# ============================================================
def checkpoint_s3_key(run_date):
    return f"bronze/_checkpoints/search_collector_{run_date}.json"


def load_checkpoint(s3, run_date):
    from botocore.exceptions import ClientError
    key = checkpoint_s3_key(run_date)
    try:
        obj = s3.get_object(Bucket=BUCKET_NAME, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            data = {}
        else:
            raise
    return {"completed_search_tasks": set(data.get("completed_search_tasks", []))}


def save_checkpoint(s3, run_date, state):
    key = checkpoint_s3_key(run_date)
    body = json.dumps(
        {"completed_search_tasks": sorted(state["completed_search_tasks"])},
        ensure_ascii=False,
    )
    s3.put_object(Bucket=BUCKET_NAME, Key=key, Body=body.encode("utf-8"), ContentType="application/json")


def task_id(category_label, duration, period_index):
    return f"{category_label}|{duration}|{period_index}"


# ============================================================
# 구간(기간) 생성 - 오늘부터 거슬러 올라가며 TOTAL_DAYS_BACK일을 카테고리별 구간으로 순회
# ============================================================
def generate_periods(window_days, total_days_back):
    """기준 시점을 '내일 자정(00:00 KST)'으로 고정 - 같은 날 여러 번 실행해도
    구간 경계가 항상 동일해서, 체크포인트가 다른 작업으로 착각하는 일이 없다."""
    today = datetime.now(KST).date()
    anchor = datetime.combine(today, datetime.min.time(), tzinfo=KST) + timedelta(days=1)
    oldest = anchor - timedelta(days=total_days_back)
    periods = []
    cursor_end = anchor
    while cursor_end > oldest:
        cursor_start = max(cursor_end - timedelta(days=window_days), oldest)
        periods.append((cursor_start, cursor_end))
        cursor_end = cursor_start
    return periods


def build_all_tasks():
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
# 1단계: 번들(동의어 묶음) 하나당 search() 호출. 번들에 태그가 여러 개면
# q="a"|"b"|"c" 형태의 OR 검색 - CATEGORY_TAGS의 번들 기준 참고.
# ============================================================
def do_search(task, tag_bundle):
    q = "|".join(f'"{t}"' for t in tag_bundle)
    published_after = task["period_start"].isoformat().replace("+00:00", "Z")
    published_before = task["period_end"].isoformat().replace("+00:00", "Z")
    params = {
        "part": "snippet",
        "type": "video",
        "q": q,
        "videoCategoryId": task["category_id"],
        "videoDuration": task["duration"],
        "order": "date",
        "publishedAfter": published_after,
        "publishedBefore": published_before,
        "regionCode": REGION_CODE,
        "relevanceLanguage": RELEVANCE_LANGUAGE,
        "maxResults": MAX_RESULTS,
    }
    return call_with_rotation("search", params)


# ============================================================
# 2단계: videos.list / channels.list 보강
# ============================================================
def enrich_videos(video_ids):
    details = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        data = call_with_rotation(
            "videos",
            {"id": ",".join(batch), "part": "snippet,contentDetails,statistics,status,topicDetails"},
        )
        details.extend(data.get("items", []))
    return details


def enrich_channels(channel_ids):
    details = []
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i + 50]
        data = call_with_rotation(
            "channels", {"id": ",".join(batch), "part": "snippet,statistics,contentDetails"}
        )
        details.extend(data.get("items", []))
    return details


# ============================================================
# 3단계: video_id 기준으로 search+videos.list+channels.list 응답을 그대로 합침
# (Bronze - duration_seconds/video_type/KST 파생/is_valid 같은 가공·검증은 Silver 단계에서)
# ============================================================
def extract_video_fact(v):
    sn = v.get("snippet", {})
    cd = v.get("contentDetails", {})
    st = v.get("statistics", {})
    status = v.get("status", {})
    topic = v.get("topicDetails", {})
    thumbnails = sn.get("thumbnails", {})
    thumbnail_url = (
        thumbnails.get("high", {}).get("url")
        or thumbnails.get("medium", {}).get("url")
        or thumbnails.get("default", {}).get("url")
        or ""
    )
    return {
        "video_id": v.get("id", ""),
        "category_id": sn.get("categoryId", ""),
        "title": sn.get("title", ""),
        "description": sn.get("description", ""),
        "published_at": sn.get("publishedAt", ""),
        "tags": sn.get("tags", []),
        "live_broadcast_content": sn.get("liveBroadcastContent", ""),
        "default_audio_language": sn.get("defaultAudioLanguage", ""),
        "thumbnail_url": thumbnail_url,
        "view_count": st.get("viewCount"),
        "like_count": st.get("likeCount"),
        "comment_count": st.get("commentCount"),
        "duration": cd.get("duration", ""),
        "definition": cd.get("definition", ""),
        "caption": cd.get("caption", ""),
        "has_paid_product_placement": cd.get("hasPaidProductPlacement", False),
        "privacy_status": status.get("privacyStatus", ""),
        "made_for_kids": status.get("madeForKids"),
        "topic_categories": topic.get("topicCategories", []),
        "channel_id": sn.get("channelId", ""),
    }


def build_flat_records(video_facts, channel_info):
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
            "matched_tags": vf.get("matched_tags", []),
            "live_broadcast_content": vf.get("live_broadcast_content", ""),
            "default_audio_language": vf.get("default_audio_language", ""),
            "thumbnail_url": vf.get("thumbnail_url", ""),
            "view_count": vf.get("view_count"),
            "like_count": vf.get("like_count"),
            "comment_count": vf.get("comment_count"),
            "duration": vf.get("duration", ""),
            "definition": vf.get("definition", ""),
            "caption": vf.get("caption", ""),
            "has_paid_product_placement": vf.get("has_paid_product_placement", False),
            "privacy_status": vf.get("privacy_status", ""),
            "made_for_kids": vf.get("made_for_kids"),
            "topic_categories": vf.get("topic_categories", []),
            "channel_id": cid,
            "channel_name": ch.get("title", ""),
            "channel_published_at": ch.get("channel_published_at", ""),
            "subscriber_count": ch.get("subscriber_count"),
            "hidden_subscriber_count": ch.get("hidden_subscriber_count"),
            "channel_total_view_count": ch.get("channel_view_count"),
            "channel_total_video_count": ch.get("channel_video_count"),
            "uploads_playlist_id": ch.get("uploads_playlist_id", ""),
            "collected_at_utc": collected_at,
        })
    return records


def bronze_s3_key(category_label, run_date, invocation_suffix):
    """카테고리(영어 슬러그) x 연-월-일 파티션 + 실행 시각으로 파일을 나눈다.
    (Silver 변환 DAG가 category=/year=/month=/day= 파티션 prefix로 나열함)"""
    slug = CATEGORY_SLUGS[category_label]
    year, month, day = run_date.split("-")
    return (
        f"bronze/search/category={slug}/year={year}/month={month}/day={day}/"
        f"{slug}_{run_date}_{invocation_suffix}.jsonl"
    )


def upload_records(s3, category_label, run_date, invocation_suffix, records):
    if not records:
        return None
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"
    key = bronze_s3_key(category_label, run_date, invocation_suffix)
    s3.put_object(Bucket=BUCKET_NAME, Key=key, Body=body.encode("utf-8"), ContentType="application/x-ndjson")
    return key


# ============================================================
# Lambda 진입점
# ============================================================
def lambda_handler(event, context):
    s3 = get_s3_client()
    run_date = datetime.now(KST).strftime("%Y-%m-%d")
    invocation_suffix = datetime.now(KST).strftime("%H%M%S")

    all_tasks = build_all_tasks()
    state = load_checkpoint(s3, run_date)
    remaining = [
        t for t in all_tasks
        if task_id(t["category_label"], t["duration"], t["period_index"])
        not in state["completed_search_tasks"]
    ]

    result = {
        "run_date": run_date,
        "total_tasks": len(all_tasks),
        "completed_before": len(state["completed_search_tasks"]),
    }

    if not remaining:
        result.update(status="complete", stop_reason=None, processed_this_invocation=0,
                       completed_total=len(state["completed_search_tasks"]), uploaded_keys=[])
        print(json.dumps(result, ensure_ascii=False))
        return result

    buffered = {}  # category_label -> [record, ...] (이번 invocation에서 새로 완료한 task들)
    written_video_ids_this_run = set()  # 이번 invocation 안에서만 유효한 경계-중복 방지
    processed = 0
    stop_reason = None

    for task in remaining:
        if time_running_low(context):
            stop_reason = "time_budget"
            break

        tid = task_id(task["category_label"], task["duration"], task["period_index"])

        video_tag_map = {}
        try:
            for tag_bundle in task["tags"]:
                resp = do_search(task, tag_bundle)
                for it in resp.get("items", []):
                    vid = it["id"]["videoId"]
                    # 번들 검색이라 어떤 태그가 걸렸는지는 API가 알려주지 않음 - 번들
                    # 안의 태그가 애초에 동의어라 구분이 무의미하므로 번들 전체를 기록
                    video_tag_map.setdefault(vid, set()).update(tag_bundle)

            video_ids = list(video_tag_map.keys())
            video_details = enrich_videos(video_ids)

            task_video_facts = []
            for v in video_details:
                vf = extract_video_fact(v)
                vf["matched_tags"] = sorted(video_tag_map.get(vf["video_id"], []))
                task_video_facts.append(vf)

            referenced_channel_ids = list({vf["channel_id"] for vf in task_video_facts})
            channel_details = enrich_channels(referenced_channel_ids)
        except QuotaExhaustedError:
            stop_reason = "quota_exhausted"
            break

        channel_info_local = {}
        for c in channel_details:
            stats = c.get("statistics", {})
            sn = c.get("snippet", {})
            cd = c.get("contentDetails", {})
            channel_info_local[c["id"]] = {
                "title": sn.get("title", ""),
                "channel_published_at": sn.get("publishedAt", ""),
                "subscriber_count": stats.get("subscriberCount", ""),
                "hidden_subscriber_count": stats.get("hiddenSubscriberCount", ""),
                "channel_view_count": stats.get("viewCount", ""),
                "channel_video_count": stats.get("videoCount", ""),
                "uploads_playlist_id": cd.get("relatedPlaylists", {}).get("uploads", ""),
            }

        records = build_flat_records(task_video_facts, channel_info_local)
        new_records = [r for r in records if r["video_id"] not in written_video_ids_this_run]
        written_video_ids_this_run.update(r["video_id"] for r in new_records)
        buffered.setdefault(task["category_label"], []).extend(new_records)

        state["completed_search_tasks"].add(tid)
        processed += 1

    uploaded_keys = []
    for label, records in buffered.items():
        key = upload_records(s3, label, run_date, invocation_suffix, records)
        if key:
            uploaded_keys.append(key)

    save_checkpoint(s3, run_date, state)

    completed_total = len(state["completed_search_tasks"])
    status = "complete" if completed_total >= len(all_tasks) else "partial"
    result.update(
        status=status,
        stop_reason=stop_reason,
        processed_this_invocation=processed,
        completed_total=completed_total,
        video_count_this_invocation=len(written_video_ids_this_run),
        uploaded_keys=uploaded_keys,
    )
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    print(lambda_handler({}, None))
