#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
frontend/scripts/build_home_summary.py
----------------------------------------
홈 탭 "AI 요약" 카드용 데이터를 만든다. frontend/mock/{video_pool,channel_pool,
upload_heatmap}.json(이 스크립트를 부르는 4시간 주기 refresh_dashboard 파이프라인이
바로 직전에 만든 것 - category_trend.json은 2026-09-08 팩트 시트에서 제외했다. 아래
참고)과 frontend/mock/{metadata_impact,topic_trends,
synthetic_demo}.json(별도의 하루 1회 analysis_refresh 람다가 만들어 이미 S3에 올려둔
것 - 이 4시간 파이프라인은 만들지 않으므로 S3에서 직접 받아온다)을 카테고리별 "팩트
시트"로 압축해서, AWS Bedrock(Claude Haiku, us.anthropic.claude-haiku-4-5-...)에 한
번씩 넘겨 3~4문장짜리 한국어 브리핑을 받는다.

Bedrock 호출은 쓰로틀링/일시 장애로 실패할 수 있는데, 이 스크립트가 비정상 종료하면
lambda/refresh_dashboard.py의 _run_script()가 RuntimeError를 던져서 전체 파이프라인
(Step Functions DashboardRefresh 단계)이 실패해버린다. "AI 요약"은 있으면 좋은
부가 기능이지 핵심 데이터 갱신을 막을 이유가 없으므로, 카테고리별로 실패하면 그
카테고리만 건너뛰고(이전 S3 값이 있으면 유지) 스크립트는 항상 정상 종료한다.

실행: repo 루트 어디서든 `python frontend/scripts/build_home_summary.py`
(frontend/mock/{video_pool,channel_pool,upload_heatmap}.json이 로컬에 이미 있어야 함
- build_dashboard_data.py를 먼저 실행. FRONTEND_S3_BUCKET 환경변수가 있으면 나머지
3개 파일을 S3에서 받아오고, 없으면 그 부분만 생략하고 진행)
"""
import datetime
import json
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.dirname(SCRIPT_DIR)
MOCK_DIR = f"{FRONTEND_DIR}/mock"
OUT_PATH = f"{MOCK_DIR}/home_summary.json"

CATEGORIES = {"gaming": "게임", "autos_vehicles": "자동차·차량", "film_animation": "영화·애니메이션"}
DAY_LABELS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
SLOT_LABELS = ["새벽", "오전", "오후", "저녁", "심야"]

# us-west-2에서 온디맨드 직접 호출이 안 되는 모델이라(테스트 결과 "inference profile
# 필요" 에러) cross-region inference profile ID(us. 프리픽스)를 그대로 모델 ID로 쓴다.
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-west-2")
BEDROCK_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

FRONTEND_BUCKET = os.environ.get("FRONTEND_S3_BUCKET")


def _load_local(name):
    path = f"{MOCK_DIR}/{name}"
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return None


def _load_from_s3(name):
    """analysis_refresh 람다(하루 1회)가 만드는 파일 - 이 4시간 파이프라인에는 없어서
    이미 S3에 올라가 있는 최신 버전을 직접 받아온다. 없거나 실패하면 None(그 데이터만
    생략하고 나머지로 진행 - 지어내지 않음)."""
    if not FRONTEND_BUCKET:
        return None
    try:
        s3 = boto3.client("s3")
        body = s3.get_object(Bucket=FRONTEND_BUCKET, Key=f"mock/{name}")["Body"].read()
        return json.loads(body)
    except (ClientError, BotoCoreError, ValueError, OSError) as e:
        print(f"{name} S3에서 못 읽음(건너뜀): {e}")
        return None


def _fmt_views(n):
    if n is None:
        return "?"
    n = int(n)
    if n >= 100000000:
        return f"{n / 100000000:.1f}억"
    if n >= 10000:
        return f"{n / 10000:.1f}만"
    return str(n)


def _best_heatmap_slot(heatmap):
    cells = [c for c in (heatmap or {}).get("cells", []) if c.get("avg_views") is not None]
    if not cells:
        return None
    best = max(cells, key=lambda c: c["avg_views"])
    day = DAY_LABELS[best["day"]] if 0 <= best.get("day", -1) < len(DAY_LABELS) else "?"
    slot = SLOT_LABELS[best["slot"]] if 0 <= best.get("slot", -1) < len(SLOT_LABELS) else "?"
    return f"{day} {slot} (평균 조회수 {_fmt_views(best['avg_views'])}, 표본 {best.get('sample_count', 0)}건)"


def build_fact_sheet(cat_key, video_pool, channel_pool, upload_heatmap,
                      metadata_impact, topic_trends, synthetic_demo):
    """원본 JSON을 그대로 프롬프트에 넣지 않고, 카테고리당 핵심 몇 줄만 뽑는다
    (토큰/비용 절약 + 모델이 근거 없는 얘기를 지어낼 여지 축소)."""
    lines = []

    pool = (video_pool or {}).get(cat_key) or []
    new_entries = [v for v in pool if (v.get("days_since_published") or 999) <= 7]
    top_new = sorted(new_entries, key=lambda v: v.get("view_count") or 0, reverse=True)[:2]
    top_overall = sorted(pool, key=lambda v: v.get("view_count") or 0, reverse=True)[:3]
    if top_new:
        lines.append("최근 7일 내 신규 영상 중 조회수 상위: " + "; ".join(
            f"'{v.get('title', '')}' ({_fmt_views(v.get('view_count'))}회)" for v in top_new
        ))
    if top_overall:
        lines.append("전체 후보 중 조회수 상위: " + "; ".join(
            f"'{v.get('title', '')}' ({_fmt_views(v.get('view_count'))}회)" for v in top_overall
        ))

    slot = _best_heatmap_slot((upload_heatmap or {}).get(cat_key))
    if slot:
        lines.append(f"업로드하기 가장 좋은 요일·시간대: {slot}")

    channels = (channel_pool or {}).get(cat_key) or []
    top_channels = sorted(channels, key=lambda c: c.get("avg_views_per_video") or 0, reverse=True)[:2]
    if top_channels:
        lines.append("영상당 평균 조회수가 높은 채널: " + "; ".join(
            f"{c.get('name', '')}(구독자 {_fmt_views(c.get('subscriber_count'))})" for c in top_channels
        ))

    # 2026-09-08: category_trend.json의 일평균 조회수/참여율은 여기 넣었더니 AI가
    # "이 숫자를 기준으로 기획하세요" 식으로 뭘 하라는 건지 불명확한 문장만 만들어내서
    # (사용자 피드백) 팩트 시트에서 뺐다 - 태그 트렌드/업로드 타이밍/메타데이터 영향처럼
    # 곧바로 실행 가능한 조언으로 이어지는 항목에만 집중시킨다. 이 숫자 자체는 어차피
    # "카테고리 트렌드" 탭에 그대로 노출되고 있어서 안 보여줘도 정보가 사라지지 않는다.

    mi = (metadata_impact or {}).get(cat_key) or {}
    sig = [f for f in (mi.get("features") or []) if f.get("significant") and f.get("effect_pct") is not None]
    # 2026-09-08: 자막처럼 실제로 "이렇게 하세요"로 바로 옮길 수 있는 유의미 요소가
    # 상위 2개 밖으로 밀려서 요약에 안 보인 적이 있어(사용자 피드백) 3개로 늘렸다.
    sig = sorted(sig, key=lambda f: abs(f["effect_pct"]), reverse=True)[:3]
    if sig:
        lines.append("성과에 유의미한 영향을 준 요소(포맷 관련 조언에 활용): " + "; ".join(
            f"{f.get('label')} ({f['effect_pct']:+.1f}%)" for f in sig
        ))

    # 2026-09-08: "요즘 뜨는 태그" top5(build_topic_trends.py가 median_views_per_day
    # 내림차순으로 이미 정렬해둠) 중 성과 상위 태그를 "이런 태그를 써보라"는 구체적인
    # 추천으로 제공 - 트렌드 변화율이 가장 큰 태그 1개만으로는 "무슨 태그를 써야
    # 하는지" 답이 안 됐다는 사용자 피드백 반영.
    tt = (topic_trends or {}).get(cat_key) or {}
    tt_clusters = tt.get("clusters") or []
    if tt_clusters:
        recommended = [
            (c.get("top_terms") or ["?"])[0] for c in tt_clusters[:3] if c.get("top_terms")
        ]
        if recommended:
            lines.append("영상당 조회수가 높은 편이라 태그로 써볼 만한 것들: " + ", ".join(f"'{t}'" for t in recommended))

    trending_clusters = [c for c in tt_clusters if c.get("trend_pct") is not None]
    trending_clusters.sort(key=lambda c: abs(c["trend_pct"]), reverse=True)
    if trending_clusters:
        c = trending_clusters[0]
        tag = (c.get("top_terms") or ["?"])[0]
        lines.append(f"요즘 가장 트렌드 변화가 큰 태그: '{tag}' ({c['trend_pct']}% 변화)")

    jt = (synthetic_demo or {}).get("judgment_timing") or {}
    r2 = jt.get("r_squared") if isinstance(jt, dict) else None
    if r2 is not None:
        lines.append(f"(심화분석 데모) 초기 성과로 최종 성과를 예측하는 신뢰도(R²) {r2}")

    return lines


def summarize_with_bedrock(client, cat_name, fact_lines):
    if not fact_lines:
        return None
    facts = "\n".join(f"- {line}" for line in fact_lines)
    prompt = (
        f"당신은 유튜브 크리에이터를 위한 데이터 대시보드의 브리핑 작성자입니다. "
        f"아래는 '{cat_name}' 카테고리에 대해 방금 집계된 지표입니다.\n\n{facts}\n\n"
        "이 지표들을 바탕으로, 크리에이터가 한눈에 읽을 수 있는 3~4문장짜리 한국어 "
        "브리핑을 작성하세요. 특히 자막 같은 포맷 요소나 추천 태그처럼 크리에이터가 "
        "바로 실행에 옮길 수 있는 항목이 지표에 있으면 반드시 구체적으로 언급하세요. "
        "숫자를 인용하되 과장하지 말고, 친근하지만 담백한 톤으로 쓰세요. 브리핑 문장만 "
        "출력하고 다른 설명은 붙이지 마세요."
    )
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        # 한국어는 토큰당 정보 밀도가 영어보다 낮아서(음절 단위로 쪼개지는 경우가 많음)
        # 300으로는 3~4문장이 끝나기 전에 잘리는 경우가 있었음(실측 확인) - 여유를 둠.
        "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}],
    })
    resp = client.invoke_model(modelId=BEDROCK_MODEL_ID, body=body, contentType="application/json")
    payload = json.loads(resp["body"].read())
    parts = payload.get("content") or []
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()
    return text or None


def main():
    video_pool = _load_local("video_pool.json")
    channel_pool = _load_local("channel_pool.json")
    upload_heatmap = _load_local("upload_heatmap.json")
    metadata_impact = _load_from_s3("metadata_impact.json")
    topic_trends = _load_from_s3("topic_trends.json")
    synthetic_demo = _load_from_s3("synthetic_demo.json")

    existing = _load_local("home_summary.json") or {}
    out = {}
    client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    report = []

    for cat_key, cat_name in CATEGORIES.items():
        fact_lines = build_fact_sheet(
            cat_key, video_pool, channel_pool, upload_heatmap,
            metadata_impact, topic_trends, synthetic_demo,
        )
        summary = None
        try:
            summary = summarize_with_bedrock(client, cat_name, fact_lines)
        except Exception as e:  # Bedrock 쓰로틀링/일시 장애 등 - 이 카테고리만 건너뜀
            print(f"{cat_key} 요약 생성 실패(건너뜀): {e}")

        if summary:
            out[cat_key] = {
                "summary": summary,
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            report.append(f"{cat_key}: 생성 완료")
        elif cat_key in existing:
            out[cat_key] = existing[cat_key]
            report.append(f"{cat_key}: 실패, 이전 값 유지({existing[cat_key].get('generated_at')})")
        else:
            report.append(f"{cat_key}: 실패, 이전 값도 없어 생략")

    out["_comment"] = (
        "홈/업로드가이드/추천채널/카테고리트렌드/심화분석(데모) 데이터를 요약해 AWS "
        f"Bedrock({BEDROCK_MODEL_ID})으로 생성한 카테고리별 브리핑. refresh_dashboard "
        "람다(4시간 주기)가 build_dashboard_data.py 다음 단계로 실행한다. Bedrock 호출이 "
        "실패한 카테고리는 이전 값을 그대로 유지하며, 이 스크립트 자체는 그 실패로 인해 "
        "비정상 종료하지 않는다(전체 대시보드 갱신 파이프라인을 막지 않기 위함)."
    )

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n".join(report))
    print(f"-> {OUT_PATH}")
    print("DONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # "AI 요약"은 부가 기능이지 핵심 데이터 갱신 조건이 아니다 - 예상 못 한 예외가
        # 나도 이 스크립트는 항상 exit 0으로 끝나서 refresh_dashboard 파이프라인 전체를
        # 실패시키지 않는다.
        print(f"build_home_summary.py 예상치 못한 오류(무시하고 종료): {e}")
