#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube search.list 기반 증분 수집 Lambda (4시간 간격 실행 전제)

동작
- 영구 체크포인트 1개(S3: bronze/_checkpoints/search_collector_state.json)에
  * searched_until : 지금까지 검색을 끝낸 시점(다음 검색의 publishedAfter)
  * known_videos   : {video_id: category_label} - 지금까지 발견한 모든 영상
  를 기록한다.
- 매 실행:
  1) search.list 로 [searched_until, now] 구간의 "신규" 영상만 발견 (카테고리별
     태그 전부를 q="a"|"b"|... OR 1회로 합쳐서 videoDuration(medium/long)당 1콜)
  2) known_videos 전체(신규 포함)를 videos.list / channels.list 로 재조회해
     현재 조회수·구독자 스냅샷을 만든다 -> 시간이 갈수록 4h치, 8h치, 12h치 ...
     시계열이 Bronze에 누적된다 (같은 영상 재수집은 의도된 동작)
  3) 카테고리별로 S3 Bronze(JSON Lines)에 업로드
  4) 전 카테고리 업로드 성공 후에만 searched_until 을 now 로 전진시키고 체크포인트 저장
     (부분 실패 시 커서를 안 움직이므로 다음 실행이 같은 구간을 다시 훑는다 = 재수집이지
      유실 아님)
- 최초 실행(체크포인트 없음): [now - INITIAL_LOOKBACK_HOURS, now] 부터 시작한다.
  일반적으로는 scripts/seed_search_checkpoint.py 로 기존 S3 데이터에서 최근 며칠치
  video_id 를 미리 채워두고 배포한다.
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

# 파티션 키(bronze/search/.../year=/month=/day=)와 체크포인트/윈도우 기준: KST(한국시간)
KST = timezone(timedelta(hours=9))

YOUTUBE_API_KEYS = [k.strip() for k in os.environ.get("YOUTUBE_API_KEYS", "").split(",") if k.strip()]
BUCKET_NAME = os.environ["BUCKET_NAME"]
REGION_CODE = os.environ.get("REGION_CODE", "KR")
RELEVANCE_LANGUAGE = os.environ.get("RELEVANCE_LANGUAGE", "ko")
MAX_RESULTS = int(os.environ.get("MAX_RESULTS", "50"))
# 체크포인트가 없을 때(콜드 스타트)만 쓰는 초기 조회 구간
INITIAL_LOOKBACK_HOURS = int(os.environ.get("INITIAL_LOOKBACK_HOURS", "8"))

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

# 카테고리별 검색어(q) 태그. 증분 구간이 4~8시간으로 짧아 slot당 결과가 search API
# maxResults 캡(50)에 한참 못 미치므로, 예전처럼 번들로 쪼갤 필요 없이 카테고리의
# 태그 전부를 q="a"|"b"|"c" ... OR 1회로 합쳐서 보낸다.
# (어떤 태그로 걸렸는지는 기록하지 않는다 - 다운스트림에서 아무도 안 씀)
CATEGORY_TAGS = {
    "영화_애니메이션": [
        "영화리뷰", "결말포함", "영화 해석", "영화 요약", "영화 비평",
        "개봉작 리뷰", "영화 몰아보기", "영화 추천", "박스오피스",
    ],
    "자동차_차량": [
        "시승기", "신차 리뷰", "전기차 리뷰", "차량 비교", "장기렌트 비교",
        "자동차 리뷰", "중고차 리뷰", "차박",
    ],
    "게임": [
        "게임리뷰", "게임 리뷰", "게임 후기", "게임 공략", "게임 업데이트",
        "게임 추천", "신작 게임", "스팀 할인", "무료 게임",
    ],
    # category_id=22는 업로더가 카테고리를 지정 안 했을 때 YouTube가 자동으로 붙이는
    # 기본값이라 Silver 단계에서 오염 데이터로 분리됨 - 다른 카테고리로 오분류된
    # 리뷰어를 잡아내는 용도.
    "인물_블로그": ["영화리뷰", "시승기", "게임리뷰"],
}

RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BASE_DELAY_SEC = 2      # 재시도 대기시간: 2, 4, 8, 16, 32초로 증가
CALL_PACING_SEC = 1.0              # 매 API 호출 뒤 최소 간격 (분당 속도 제한 예방)

CHECKPOINT_KEY = "bronze/_checkpoints/search_collector_state.json"

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
# 체크포인트 (S3) - 영구 파일 1개
# ============================================================
def load_state(s3):
    """{'searched_until': <iso str|None>, 'known_videos': {video_id: category_label}}.
    체크포인트 객체가 없을 때만(NoSuchKey/404) 콜드 스타트로 취급한다.
    그 외 S3 오류(일시적 5xx, throttle, 권한 등)는 절대 '빈 상태'로 뭉개지 말고
    그대로 raise 한다 - 잘못 삼키면 커서가 리셋된다."""
    from botocore.exceptions import ClientError
    try:
        obj = s3.get_object(Bucket=BUCKET_NAME, Key=CHECKPOINT_KEY)
        data = json.loads(obj["Body"].read().decode("utf-8"))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            print("체크포인트 없음 - 콜드 스타트")
            data = {}
        else:
            raise
    return {
        "searched_until": data.get("searched_until"),
        "known_videos": dict(data.get("known_videos", {})),
    }


def save_state(s3, state):
    body = json.dumps(
        {
            "searched_until": state["searched_until"],
            "known_videos": dict(sorted(state["known_videos"].items())),
        },
        ensure_ascii=False,
    )
    s3.put_object(
        Bucket=BUCKET_NAME, Key=CHECKPOINT_KEY,
        Body=body.encode("utf-8"), ContentType="application/json",
    )


def compute_window(state, now):
    """이번 실행이 검색할 [start, end). end = 현재 시각(정시 절삭)."""
    if state["searched_until"]:
        start = datetime.fromisoformat(state["searched_until"])
    else:
        start = now - timedelta(hours=INITIAL_LOOKBACK_HOURS)
    return start, now


# ============================================================
# 1단계: 증분 발견 - 카테고리의 태그 전부를 OR 1회로
# ============================================================
def do_search(category_id, duration, tags, published_after, published_before):
    q = "|".join(f'"{t}"' for t in tags)
    params = {
        "part": "snippet",
        "type": "video",
        "q": q,
        "videoCategoryId": category_id,
        "videoDuration": duration,
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
            "channels",
            {
                "id": ",".join(batch),
                # topicDetails/brandingSettings는 channels.list 쿼터(1유닛)에 영향 없음 -
                # 채널 주제·키워드는 오분류 채널 판별(특히 category_id=22) 신호로 씀
                "part": "snippet,statistics,contentDetails,topicDetails,brandingSettings",
            },
        )
        details.extend(data.get("items", []))
    return details


# ============================================================
# 3단계: video_id 기준으로 videos.list+channels.list 응답을 그대로 합침
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
        "default_language": sn.get("defaultLanguage", ""),
        "thumbnail_url": thumbnail_url,
        "view_count": st.get("viewCount"),
        "like_count": st.get("likeCount"),
        "comment_count": st.get("commentCount"),
        "duration": cd.get("duration", ""),
        "definition": cd.get("definition", ""),
        "caption": cd.get("caption", ""),
        "licensed_content": cd.get("licensedContent"),
        "content_rating_yt": cd.get("contentRating", {}).get("ytRating", ""),
        "region_restriction": cd.get("regionRestriction", {}),
        "has_paid_product_placement": cd.get("hasPaidProductPlacement", False),
        "privacy_status": status.get("privacyStatus", ""),
        "license": status.get("license", ""),
        "made_for_kids": status.get("madeForKids"),
        "topic_categories": topic.get("topicCategories", []),
        "channel_id": sn.get("channelId", ""),
    }


def build_channel_info(channel_details):
    info = {}
    for c in channel_details:
        stats = c.get("statistics", {})
        sn = c.get("snippet", {})
        cd = c.get("contentDetails", {})
        topic = c.get("topicDetails", {})
        branding_channel = c.get("brandingSettings", {}).get("channel", {})
        ch_thumbs = sn.get("thumbnails", {})
        ch_thumbnail_url = (
            ch_thumbs.get("high", {}).get("url")
            or ch_thumbs.get("medium", {}).get("url")
            or ch_thumbs.get("default", {}).get("url")
            or ""
        )
        info[c["id"]] = {
            "title": sn.get("title", ""),
            "channel_description": sn.get("description", ""),
            "channel_custom_url": sn.get("customUrl", ""),
            "channel_country": sn.get("country", ""),
            "channel_published_at": sn.get("publishedAt", ""),
            "channel_thumbnail_url": ch_thumbnail_url,
            "channel_topic_categories": topic.get("topicCategories", []),
            "channel_keywords": branding_channel.get("keywords", ""),
            "subscriber_count": stats.get("subscriberCount", ""),
            "hidden_subscriber_count": stats.get("hiddenSubscriberCount", ""),
            "channel_view_count": stats.get("viewCount", ""),
            "channel_video_count": stats.get("videoCount", ""),
            "uploads_playlist_id": cd.get("relatedPlaylists", {}).get("uploads", ""),
        }
    return info


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
            "live_broadcast_content": vf.get("live_broadcast_content", ""),
            "default_audio_language": vf.get("default_audio_language", ""),
            "default_language": vf.get("default_language", ""),
            "thumbnail_url": vf.get("thumbnail_url", ""),
            "view_count": vf.get("view_count"),
            "like_count": vf.get("like_count"),
            "comment_count": vf.get("comment_count"),
            "duration": vf.get("duration", ""),
            "definition": vf.get("definition", ""),
            "caption": vf.get("caption", ""),
            "licensed_content": vf.get("licensed_content"),
            "content_rating_yt": vf.get("content_rating_yt", ""),
            "region_restriction": vf.get("region_restriction", {}),
            "has_paid_product_placement": vf.get("has_paid_product_placement", False),
            "privacy_status": vf.get("privacy_status", ""),
            "license": vf.get("license", ""),
            "made_for_kids": vf.get("made_for_kids"),
            "topic_categories": vf.get("topic_categories", []),
            "channel_id": cid,
            "channel_name": ch.get("title", ""),
            "channel_description": ch.get("channel_description", ""),
            "channel_custom_url": ch.get("channel_custom_url", ""),
            "channel_country": ch.get("channel_country", ""),
            "channel_thumbnail_url": ch.get("channel_thumbnail_url", ""),
            "channel_topic_categories": ch.get("channel_topic_categories", []),
            "channel_keywords": ch.get("channel_keywords", ""),
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
    now = datetime.now(KST).replace(minute=0, second=0, microsecond=0)
    run_date = datetime.now(KST).strftime("%Y-%m-%d")
    invocation_suffix = datetime.now(KST).strftime("%H%M%S")

    state = load_state(s3)
    win_start, win_end = compute_window(state, now)

    result = {
        "window": [win_start.isoformat(), win_end.isoformat()],
        "known_before": len(state["known_videos"]),
    }

    stop_reason = None

    # --- 1) 증분 발견 (신규 video_id 를 known_videos 에 등록) ---
    newly_found = 0
    try:
        if win_start < win_end:
            after = win_start.isoformat().replace("+00:00", "Z")
            before = win_end.isoformat().replace("+00:00", "Z")
            for label, cat_id in CATEGORY_IDS.items():
                for duration in SEARCH_VIDEO_DURATIONS:
                    resp = do_search(cat_id, duration, CATEGORY_TAGS[label], after, before)
                    for it in resp.get("items", []):
                        vid = it["id"]["videoId"]
                        if vid not in state["known_videos"]:
                            state["known_videos"][vid] = label
                            newly_found += 1
        else:
            print(f"검색 구간 없음(start={win_start} >= end={win_end}) - 갱신만 진행")
    except QuotaExhaustedError:
        stop_reason = "quota_exhausted"

    # --- 2) known_videos 전체 스냅샷 (videos.list / channels.list) ---
    records_by_label = {}
    snapshot_count = 0
    if stop_reason is None:
        all_ids = sorted(state["known_videos"])
        try:
            facts = [extract_video_fact(v) for v in enrich_videos(all_ids)]
            channel_ids = sorted({f["channel_id"] for f in facts if f["channel_id"]})
            channel_info = build_channel_info(enrich_channels(channel_ids))
            for r in build_flat_records(facts, channel_info):
                # 파티션(파일 위치)은 이 영상을 잡아낸 검색 카테고리 기준.
                # 레코드 안의 category_id/category_name 은 YouTube 가 알려준 실제 값 그대로.
                label = state["known_videos"].get(r["video_id"], "인물_블로그")
                records_by_label.setdefault(label, []).append(r)
                snapshot_count += 1
        except QuotaExhaustedError:
            stop_reason = "quota_exhausted"

    # --- 3) 업로드 ---
    uploaded_keys = []
    for label, records in records_by_label.items():
        key = upload_records(s3, label, run_date, invocation_suffix, records)
        if key:
            uploaded_keys.append(key)

    # --- 4) 커서 전진은 '검색 완주 + 업로드 성공' 이후에만 ---
    upload_ok = len(uploaded_keys) == len(records_by_label)
    if stop_reason is None and win_start < win_end and upload_ok:
        state["searched_until"] = win_end.isoformat()
    save_state(s3, state)

    result.update(
        stop_reason=stop_reason,
        newly_found=newly_found,
        snapshot_count=snapshot_count,
        known_after=len(state["known_videos"]),
        searched_until=state["searched_until"],
        uploaded_keys=uploaded_keys,
    )
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    print(lambda_handler({}, None))
