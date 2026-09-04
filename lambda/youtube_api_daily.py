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
     태그 전부를 q="a"|"b"|... OR 1회로 합쳐서 videoDuration(medium/long)당 1콜,
     결과가 50을 넘으면 nextPageToken을 SEARCH_MAX_PAGES까지 따라감)
  1b) chart=mostPopular(TRENDING_CATEGORY_IDS)에서 쇼츠가 아니고 게시 4일 이내이며
     조회수/구독자 비율이 TRENDING_SUB_RATIO_MIN 이상인 "구독자 대비 떡상" 영상만
     추가로 known_videos에 넣는다(발견 시점 비율은 state["trending"]에 기록 ->
     스냅샷 레코드의 discovered_via="trending", trending_sub_ratio 로 노출).
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
- known_videos 는 영원히 안 쌓인다: 매 실행마다 스냅샷하면서 (1) 게시일이
  KNOWN_VIDEO_MAX_AGE_DAYS 를 넘었거나 (2) videos.list 응답에서 아예 사라진(삭제/비공개
  전환 추정) video_id 를 제거한다.
- Lambda 남은 실행시간이 TIME_BUDGET_SAFETY_SEC 밑으로 떨어지면, videos.list/channels.list
  배치 처리 중이라도 그 시점까지 모은 것만 우아하게 저장하고 종료한다(TimeBudgetExceeded).
  다음 실행이 나머지를 이어서 스냅샷한다 - known_videos 자체는 그대로 남아있어 유실 없음.
- 표준 라이브러리(urllib)만 사용 -> googleapiclient 없이 Lambda Layer 불필요.
"""

import json
import os
import re
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
# 게시일이 이보다 오래된 영상은 known_videos에서 제거(더 이상 재조회 안 함) -
# spec.md의 "30일 초과: 수집 중단" 기준과 동일
KNOWN_VIDEO_MAX_AGE_DAYS = int(os.environ.get("KNOWN_VIDEO_MAX_AGE_DAYS", "30"))
# 남은 실행시간이 이 값(초) 밑으로 떨어지면 videos.list/channels.list 배치를 더 안 부르고
# 지금까지 모은 것만 저장 후 종료
TIME_BUDGET_SAFETY_SEC = int(os.environ.get("TIME_BUDGET_SAFETY_SEC", "60"))

SEARCH_VIDEO_DURATIONS = ["medium", "long"]  # 쇼츠(0~4분 전체) 제외
# search.list 결과가 slot당 maxResults(50)를 넘을 때 nextPageToken을 몇 페이지까지
# 따라갈지. 태그를 늘리면 게임 같은 카테고리는 4h 구간에도 50을 넘길 수 있어
# 뒤쪽(오래된 쪽)이 잘려나간다 - 커서가 이미 전진한 뒤라 다음 실행도 그 구간을
# 다시 안 훑으므로 영구 사각지대가 됨.
# 비용: search.list는 페이지당 100유닛. 2페이지 x 8 slot = 최악 1600유닛/run,
# 6회/일이면 최악 9600유닛/일(키 1개 한도 10k에 근접) - 단 2번째 페이지는 해당
# slot이 4h에 50건을 넘을 때만 호출되므로 실측은 대부분 1페이지(800유닛/run)에
# 머문다. 여러 키 로테이션이 안전망. 빠듯하면 env로 1로 낮출 것.
SEARCH_MAX_PAGES = int(os.environ.get("SEARCH_MAX_PAGES", "2"))

# ------------------------------------------------------------
# 인기 급상승(chart=mostPopular) 기반 '구독자 대비 떡상' 신규 영상 추적
# ------------------------------------------------------------
# 태그 검색과 별개 축: YouTube가 급상승으로 띄운 영상 중, (1) 쇼츠 아님
# (2) 게시 TRENDING_MAX_AGE_DAYS일 이내의 신선한 영상 (3) 조회수 / 구독자수가
# TRENDING_SUB_RATIO_MIN 이상 - 즉 "채널 구독자 규모로는 설명이 안 되는" 영상만
# known_videos에 추가한다. 구독자 대비 그냥 나올 만한 조회수(비율 ~1)는 신호가
# 아니므로 버린다. 추가된 영상은 state["trending"]에 발견 시점 비율을 남겨서,
# 이후 스냅샷 레코드에 discovered_via="trending"으로 표시된다.
#
# 주의: 예전에 삭제된 mostPopular 수집 경로와 달리, 여기서 찾은 video_id는 별도
# 파일/스키마가 아니라 known_videos -> 기존 스냅샷 -> 같은 Bronze 레코드로
# 일원화되어 흐른다. 쇼츠 필터는 이 함수가 유일한 게이트다(Silver는 쇼츠를
# video_type='short'로 라벨만 하고 걸러내지 않음).
TRENDING_ENABLED = os.environ.get("TRENDING_ENABLED", "1") == "1"
# mostPopular + videoCategoryId 조합을 지원하는 카테고리만. 22(인물_블로그)는
# Silver가 category_id=22를 오염으로 reject하므로 제외.
TRENDING_CATEGORY_IDS = [
    c.strip() for c in os.environ.get("TRENDING_CATEGORY_IDS", "1,2,20").split(",") if c.strip()
]
TRENDING_MAX_AGE_DAYS = int(os.environ.get("TRENDING_MAX_AGE_DAYS", "4"))
TRENDING_SUB_RATIO_MIN = float(os.environ.get("TRENDING_SUB_RATIO_MIN", "5.0"))
TRENDING_MIN_DURATION_SEC = 240  # Silver get_video_type()의 short 경계와 동일

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
        # 2026-09-04 확장: 어휘가 다른 리뷰/요약 영상까지 포착
        "영화 결말", "스포 주의", "넷플릭스 추천", "OTT 추천", "드라마 리뷰",
        "애니 리뷰", "애니메이션 추천", "신작 영화", "영화 정보",
    ],
    "자동차_차량": [
        "시승기", "신차 리뷰", "전기차 리뷰", "차량 비교", "장기렌트 비교",
        "자동차 리뷰", "중고차 리뷰", "차박",
        # 2026-09-04 확장
        "국산차 리뷰", "수입차 리뷰", "출고기", "자동차 뉴스", "신차 공개",
        "제로백", "하이브리드 리뷰", "SUV 리뷰",
    ],
    "게임": [
        "게임리뷰", "게임 리뷰", "게임 후기", "게임 공략", "게임 업데이트",
        "게임 추천", "신작 게임", "스팀 할인", "무료 게임",
        # 2026-09-04 확장
        "게임 플레이", "플레이 영상", "게임 실황", "공략집", "게임 뉴스",
        "인디게임", "콘솔 게임", "모바일 게임", "신작 리뷰",
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

# refresh_dashboard Lambda가 mock을 만든 직후, 대시보드에 실제로 노출되는 video_id
# 전부를 {video_id: category_label}로 여기에 덮어쓴다(~200개). 이 수집기는 매 실행마다
# 이 목록을 읽어서 known_videos와 합쳐 스냅샷한다 - 백필/스테디(30일+) 영상이라
# known_videos에서 빠진 것도, 화면에 떠 있는 동안은 조회수가 계속 갱신되게 하려는 것.
# known_videos와 달리 나이(KNOWN_VIDEO_MAX_AGE_DAYS)로는 안 지우고, videos.list에서
# 아예 사라진(삭제) 경우만 그 회차 스냅샷에서 빠진다. 화면에서 내려가면 다음 refresh가
# 이 파일에서 빼주므로 무한히 안 쌓인다.
PINNED_KEY = "bronze/_checkpoints/pinned_video_ids.json"

s3_client = None
cloudwatch_client = None


def get_s3_client():
    global s3_client
    if s3_client is None:
        import boto3
        s3_client = boto3.client("s3")
    return s3_client


def get_cloudwatch_client():
    global cloudwatch_client
    if cloudwatch_client is None:
        import boto3
        cloudwatch_client = boto3.client("cloudwatch")
    return cloudwatch_client


# ============================================================
# 키 로테이션 + 속도제한/할당량 구분
# ============================================================
class QuotaExhaustedError(Exception):
    """보유한 모든 API 키의 하루 할당량이 소진됨"""


class TimeBudgetExceeded(Exception):
    """Lambda 남은 실행시간이 TIME_BUDGET_SAFETY_SEC 밑으로 떨어짐"""


def time_running_low(context):
    """context가 없으면(로컬 실행) 시간 제한 없음 취급."""
    if context is None or not hasattr(context, "get_remaining_time_in_millis"):
        return False
    return context.get_remaining_time_in_millis() < TIME_BUDGET_SAFETY_SEC * 1000


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

# YouTube Data API v3 쿼터 단가(유닛) - part 파라미터와 무관하게 엔드포인트당 고정.
# spec.md "8. 수집 전략과 쿼터" 참고: search.list=100, videos.list/channels.list=1.
QUOTA_COST_PER_CALL = {"search": 100, "videos": 1, "channels": 1}
quota_units_used = 0  # 이번 Lambda 실행(invocation) 동안 누적 - lambda_handler 끝에서 CloudWatch로 전송


def call_with_rotation(path, params):
    """분당 속도 제한이면 지수 백오프로 같은 키 재시도, 하루 할당량 소진이면 다음 키로 전환.
    성공 시 응답 dict 반환. 모든 키 소진 시 QuotaExhaustedError."""
    global _current_key_index, quota_units_used
    while _current_key_index < len(YOUTUBE_API_KEYS):
        key = YOUTUBE_API_KEYS[_current_key_index]
        rate_limit_retries = 0
        while True:
            try:
                data = _api_get(path, params, key)
                quota_units_used += QUOTA_COST_PER_CALL.get(path, 0)
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
    # refresh_dashboard가 쓴 핀 목록(대시보드에 노출 중인 video_id). 없으면 빈 dict -
    # 첫 배포 직후엔 아직 없을 수 있고, 그땐 known_videos만 스냅샷(다음 회차부터 합쳐짐).
    pinned = {}
    try:
        pobj = s3.get_object(Bucket=BUCKET_NAME, Key=PINNED_KEY)
        pinned = dict(json.loads(pobj["Body"].read().decode("utf-8")))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
            raise  # 일시적 오류를 '핀 없음'으로 뭉개지 않는다
        print("핀 목록 없음 - known_videos만 스냅샷")

    return {
        "searched_until": data.get("searched_until"),
        "known_videos": dict(data.get("known_videos", {})),
        # {video_id: 발견 시점 조회수/구독자 비율} - discover_trending()이 채운다.
        # known_videos의 부분집합이며, known_videos에서 evict될 때 같이 제거된다.
        "trending": dict(data.get("trending", {})),
        # {video_id: category_label} - refresh_dashboard가 소유. 이 수집기는 읽기만 한다.
        "pinned": pinned,
    }


def save_state(s3, state):
    body = json.dumps(
        {
            "searched_until": state["searched_until"],
            "known_videos": dict(sorted(state["known_videos"].items())),
            "trending": dict(sorted(state.get("trending", {}).items())),
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
    # nextPageToken을 SEARCH_MAX_PAGES까지 따라가 slot의 뒷부분(오래된 쪽)이
    # 잘려나가는 사각지대를 막는다. 합쳐서 단일 응답 모양({"items": [...]})으로 반환.
    items = []
    for _ in range(SEARCH_MAX_PAGES):
        data = call_with_rotation("search", params)
        items.extend(data.get("items", []))
        token = data.get("nextPageToken")
        if not token:
            break
        params = dict(params, pageToken=token)
    return {"items": items}


# ============================================================
# 2단계: videos.list / channels.list 보강
# ============================================================
def enrich_videos(video_ids, context):
    """남은 실행시간이 부족해지면 그때까지 모은 것만 반환하고 complete=False.
    (호출부가 부분 결과를 그대로 활용 - 이미 받은 배치를 버리지 않는다)"""
    details = []
    for i in range(0, len(video_ids), 50):
        if time_running_low(context):
            return details, False
        batch = video_ids[i:i + 50]
        data = call_with_rotation(
            "videos",
            {"id": ",".join(batch), "part": "snippet,contentDetails,statistics,status,topicDetails"},
        )
        details.extend(data.get("items", []))
    return details, True


def enrich_channels(channel_ids, context):
    """enrich_videos와 동일한 시간 예산 규칙."""
    details = []
    for i in range(0, len(channel_ids), 50):
        if time_running_low(context):
            return details, False
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
    return details, True


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


def build_flat_records(video_facts, channel_info, trending_meta=None):
    trending_meta = trending_meta or {}
    records = []
    collected_at = datetime.now(timezone.utc).isoformat()
    for vf in video_facts:
        cid = vf.get("channel_id", "")
        ch = channel_info.get(cid, {})
        vid = vf.get("video_id", "")
        trend_ratio = trending_meta.get(vid)
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
            # 이 영상을 어떻게 발견했나: 태그 검색("search") vs 인기 급상승("trending").
            # trending이면 발견 시점의 조회수/구독자 비율도 같이 남긴다(임계값은
            # TRENDING_SUB_RATIO_MIN, 다운스트림에서 자유롭게 재필터 가능).
            "discovered_via": "trending" if trend_ratio is not None else "search",
            "trending_sub_ratio": trend_ratio,
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
# 1b단계: 인기 급상승에서 '구독자 대비 떡상' 신규 영상 발굴
# ============================================================
def _safe_int(v):
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _iso8601_duration_seconds(s):
    """PT#H#M#S -> 초. Silver parse_iso8601_duration()과 동일 규칙."""
    if not s or not isinstance(s, str):
        return None
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s)
    if not m:
        return None
    h, mi, se = m.groups()
    total = int(h or 0) * 3600 + int(mi or 0) * 60 + int(se or 0)
    return total or None


def discover_trending(state, context):
    """chart=mostPopular 목록에서 (1) 쇼츠 아님 (2) 게시 TRENDING_MAX_AGE_DAYS일 이내
    (3) 조회수/구독자 >= TRENDING_SUB_RATIO_MIN 인 신규 영상만 known_videos + state['trending']
    에 추가하고, 추가한 개수를 반환한다."""
    now_utc = datetime.now(timezone.utc)
    age_cutoff = now_utc - timedelta(days=TRENDING_MAX_AGE_DAYS)

    # {video_id: (channel_id, view_count, category_label)}
    candidates = {}
    for cat_id in TRENDING_CATEGORY_IDS:
        label = CATEGORY_ID_TO_LABEL.get(cat_id)
        if not label:
            continue
        if time_running_low(context):
            raise TimeBudgetExceeded()
        try:
            data = call_with_rotation("videos", {
                "chart": "mostPopular",
                "videoCategoryId": cat_id,
                "regionCode": REGION_CODE,
                "maxResults": MAX_RESULTS,
                "part": "snippet,contentDetails,statistics",
            })
        except RuntimeError as e:
            # 일부 카테고리는 mostPopular 미지원 -> videoChartNotFound(400)
            print(f"    트렌딩 조회 건너뜀(cat={cat_id}): {e}")
            continue
        for it in data.get("items", []):
            vid = it.get("id")
            if not vid or vid in state["known_videos"]:
                continue
            sn = it.get("snippet", {})
            dur = _iso8601_duration_seconds(it.get("contentDetails", {}).get("duration"))
            if dur is None or dur < TRENDING_MIN_DURATION_SEC:
                continue  # 쇼츠/불명 - 이 함수가 유일한 쇼츠 게이트다
            try:
                pub_dt = datetime.fromisoformat(sn.get("publishedAt", "").replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if pub_dt < age_cutoff:
                continue  # 신선하지 않음
            vc = _safe_int(it.get("statistics", {}).get("viewCount"))
            if vc is None:
                continue
            candidates[vid] = (sn.get("channelId", ""), vc, label)

    if not candidates:
        return 0

    # 후보 채널 구독자수 조회 (1유닛/배치)
    ch_ids = sorted({c for (c, _v, _l) in candidates.values() if c})
    subs = {}
    for i in range(0, len(ch_ids), 50):
        if time_running_low(context):
            raise TimeBudgetExceeded()
        data = call_with_rotation("channels", {"id": ",".join(ch_ids[i:i + 50]), "part": "statistics"})
        for c in data.get("items", []):
            st = c.get("statistics", {})
            if str(st.get("hiddenSubscriberCount")).lower() == "true":
                continue
            subs[c["id"]] = _safe_int(st.get("subscriberCount"))

    added = 0
    for vid, (cid, vc, label) in candidates.items():
        sub = subs.get(cid)
        if not sub or sub <= 0:
            continue  # 구독자 비공개/0 -> 비율 판단 불가
        ratio = vc / sub
        if ratio < TRENDING_SUB_RATIO_MIN:
            continue  # 구독자 규모로 설명되는 조회수 -> 신호 아님
        state["known_videos"][vid] = label
        state["trending"][vid] = round(ratio, 2)
        added += 1
    return added


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
                    if time_running_low(context):
                        raise TimeBudgetExceeded()
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
    except TimeBudgetExceeded:
        stop_reason = "time_budget"

    # --- 1b) 인기 급상승에서 '구독자 대비 떡상' 신규 영상 추가 ---
    trending_added = 0
    if stop_reason is None and TRENDING_ENABLED:
        try:
            trending_added = discover_trending(state, context)
        except QuotaExhaustedError:
            stop_reason = "quota_exhausted"
        except TimeBudgetExceeded:
            stop_reason = "time_budget"

    # --- 2) known_videos 전체 스냅샷 (videos.list / channels.list) ---
    records_by_label = {}
    snapshot_count = 0
    evicted_count = 0
    if stop_reason is None:
        # known_videos + 핀(대시보드 노출 중) 합집합을 스냅샷한다.
        all_ids = sorted(set(state["known_videos"]) | set(state["pinned"]))
        try:
            video_items, videos_complete = enrich_videos(all_ids, context)
            if not videos_complete:
                stop_reason = "time_budget"
            facts = [extract_video_fact(v) for v in video_items]

            # --- known_videos 정리: 게시일이 오래됐거나(30일+) 응답에서 아예 사라진
            #     (삭제/비공개 전환 추정) video_id는 더 이상 추적 안 함.
            #     시간 부족으로 이번에 못 받아온 나머지는 손대지 않고 다음 실행에 재시도. ---
            now_utc = datetime.now(timezone.utc)
            age_cutoff = now_utc - timedelta(days=KNOWN_VIDEO_MAX_AGE_DAYS)
            returned_ids = set()
            for f in facts:
                returned_ids.add(f["video_id"])
                pub_at = f.get("published_at")
                if not pub_at:
                    continue
                try:
                    pub_dt = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
                except ValueError:
                    continue
                # 핀은 나이로 안 지운다(대시보드에 떠 있는 동안은 계속 갱신). 진짜
                # 삭제(응답에서 사라짐)만 아래 videos_complete 블록에서 known_videos에서 빠짐.
                if (
                    pub_dt < age_cutoff
                    and f["video_id"] in state["known_videos"]
                    and f["video_id"] not in state["pinned"]
                ):
                    del state["known_videos"][f["video_id"]]
                    state["trending"].pop(f["video_id"], None)
                    evicted_count += 1
            if videos_complete:
                # 요청한 all_ids 중 응답에 아예 없던 것만 "사라짐"으로 간주.
                # (부분 결과일 땐 안 받아온 것과 삭제된 것을 구분 못 하므로 건드리지 않음)
                for vid in set(all_ids) - returned_ids:
                    if vid in state["known_videos"]:
                        del state["known_videos"][vid]
                        state["trending"].pop(vid, None)
                        evicted_count += 1

            channel_ids = sorted({f["channel_id"] for f in facts if f["channel_id"]})
            channel_details, channels_complete = enrich_channels(channel_ids, context)
            if not channels_complete:
                stop_reason = "time_budget"
            channel_info = build_channel_info(channel_details)
            for r in build_flat_records(facts, channel_info, state["trending"]):
                if r["channel_id"] not in channel_info:
                    # 시간 부족으로 채널 정보를 못 받아온 영상 - 빈 채널 필드로 Silver/Postgres를
                    # 오염시키지 않도록 이번엔 건너뛰고 다음 실행에서 다시 처리
                    continue
                # 파티션(파일 위치): known_videos(검색 카테고리) -> 핀 목록(대시보드
                # 카테고리) -> YouTube 실제 category_id 순으로 라벨을 정한다.
                # 레코드 안의 category_id/category_name 은 YouTube 가 알려준 실제 값 그대로.
                label = (
                    state["known_videos"].get(r["video_id"])
                    or state["pinned"].get(r["video_id"])
                    or CATEGORY_ID_TO_LABEL.get(r.get("category_id", ""))
                    or "인물_블로그"
                )
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
        trending_added=trending_added,
        trending_tracked=len(state["trending"]),
        pinned_count=len(state["pinned"]),
        snapshot_count=snapshot_count,
        evicted_count=evicted_count,
        known_after=len(state["known_videos"]),
        searched_until=state["searched_until"],
        uploaded_keys=uploaded_keys,
        quota_units_used=quota_units_used,
    )
    print(json.dumps(result, ensure_ascii=False))
    emit_run_metrics(quota_units_used, snapshot_count)
    return result


def emit_run_metrics(quota_used, bronze_records_written):
    """CloudWatch 커스텀 메트릭(수치) - 알람의 "에러 유무"만으로는 안 보이는 것들:
    이번 실행에서 실제로 쓴 YouTube API 쿼터(일일 10,000 예산 대비 추세를 보려고)와
    Bronze에 실제로 기록한 레코드 수(API가 조용히 0건만 돌려주는 이상 상황은 에러가
    아니라서 기존 알람에 안 잡힘). 메트릭 전송 실패가 수집 자체를 실패시키면 안 되므로
    예외를 삼킨다(관측 대상이지 핵심 로직이 아님)."""
    try:
        get_cloudwatch_client().put_metric_data(
            Namespace="Pipeline/PJT",
            MetricData=[
                {"MetricName": "YouTubeApiQuotaUsed", "Value": float(quota_used), "Unit": "Count"},
                {"MetricName": "BronzeRecordsWritten", "Value": float(bronze_records_written), "Unit": "Count"},
            ],
        )
    except Exception as e:  # noqa: BLE001 - 관측용 부가 기능, 본 실행을 절대 막지 않음
        print(f"CloudWatch 메트릭 전송 실패(무시하고 계속): {e}")


if __name__ == "__main__":
    print(lambda_handler({}, None))
