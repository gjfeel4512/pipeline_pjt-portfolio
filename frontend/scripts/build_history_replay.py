#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
frontend/scripts/build_history_replay.py
------------------------------------------
"화면 변화가 밋밋하다"는 피드백에 대한 대응. 원인은 실측 데이터에 변화가 없어서가
아니라, 지금 실시간 화면이 "가장 최근 수집 vs 그 직전"처럼 짧은 구간만 비교하기
때문 - youtube_api_daily.py가 4시간마다 수집해도 fact_video_snapshot의
UNIQUE(video_id, collected_date)가 날짜 단위라 하루 안의 여러 수집이 마지막
한 건으로 뭉개짐(2026-09-03 기준 실측 영상 96%가 스냅샷 1개뿐, 근본 해결은 스냅샷
키를 시간 단위로 세분화하는 것 - 별도 과제).

당장은 이미 모아둔 1년치 백필(outputs/silver/silver_{category}_{YYYY-MM}.jsonl,
2025-09~2026-09, youtube_api_collector.py 산출물)을 월별로 재생(replay)하면 실측
데이터만으로도 진짜 변화를 보여줄 수 있다. 합성 아님 - 100% 실측 Silver 데이터.

카테고리 22(people_blogs)는 lambda/stepfn_transform_search_silver.py의
CONTAMINATED_CATEGORY_IDS와 같은 기준으로 애초에 is_valid=false라 자동 제외된다
(로드 시 is_valid 필터로 걸러짐 - 별도 처리 불필요).

mover_highlight: 옵션2(변화율 프레이밍) - 절대 레벨 대신 "가장 크게 움직인 인접
두 달"을 뽑는다. 표본 5건 미만인 달은 우연한 튐일 수 있어 후보에서 제외(지어내지
않는다는 이 리포의 기존 원칙, build_topic_trends.py의 MIN_HALF_SAMPLE과 동일 취지).

출력: frontend/mock/history_replay.json
실행: repo 루트 어디서든 `python frontend/scripts/build_history_replay.py`
"""
import glob
import json
import os
from collections import defaultdict
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.dirname(SCRIPT_DIR)
ROOT = os.path.dirname(FRONTEND_DIR)
SILVER_DIR = f"{ROOT}/outputs/silver"
OUT_PATH = f"{FRONTEND_DIR}/mock/history_replay.json"

CATEGORIES = {"gaming": "게임", "autos_vehicles": "자동차·차량", "film_animation": "영화·애니메이션"}
MIN_MONTH_SAMPLE = 5


def load_category_rows(cat_key):
    pattern = f"{SILVER_DIR}/silver_{cat_key}_*.jsonl"
    rows = []
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if not d.get("is_valid"):
                    continue
                if not d.get("published_year_month"):
                    continue
                rows.append(d)
    return rows


def build_category(cat_key):
    rows = load_category_rows(cat_key)
    if not rows:
        return None

    by_month = defaultdict(list)
    for d in rows:
        by_month[d["published_year_month"]].append(d)

    months = []
    for ym in sorted(by_month.keys()):
        recs = by_month[ym]
        views = [r.get("view_count") or 0 for r in recs]
        total_views = sum(views)
        top = max(recs, key=lambda r: r.get("view_count") or 0)
        eng = [r["engagement_rate"] for r in recs if r.get("engagement_rate") is not None]
        months.append({
            "year_month": ym,
            "video_count": len(recs),
            "total_views": total_views,
            "avg_views": round(total_views / len(recs), 1),
            "avg_engagement_rate": round(sum(eng) / len(eng), 5) if eng else None,
            "top_video": {
                "video_id": top.get("video_id"),
                "title": top.get("title"),
                "channel_name": top.get("channel_name"),
                "view_count": top.get("view_count"),
            },
        })

    # 이번 달(스크립트 실행 시점 기준)은 아직 안 끝났으니 total_views가 구조적으로
    # 작게 나온다 - mover_highlight 후보에서 빼서 "달이 아직 안 끝나서 적어 보이는 것"을
    # "극적으로 줄었다"로 착각하지 않게 한다. months 배열에는 그대로 남겨서(is_partial=true)
    # 화면에는 보여주되 "집계 중"이라고 표시할 수 있게 한다.
    current_ym = datetime.now().strftime("%Y-%m")
    for m in months:
        m["is_partial"] = (m["year_month"] == current_ym)

    stable = [m for m in months if m["video_count"] >= MIN_MONTH_SAMPLE and not m["is_partial"]]
    mover = None
    for i in range(1, len(stable)):
        prev, cur = stable[i - 1], stable[i]
        if prev["total_views"] <= 0:
            continue
        pct = round((cur["total_views"] - prev["total_views"]) / prev["total_views"] * 100, 1)
        if mover is None or abs(pct) > abs(mover["mom_change_pct"]):
            mover = {
                "from_month": prev["year_month"],
                "to_month": cur["year_month"],
                "mom_change_pct": pct,
                "from_total_views": prev["total_views"],
                "to_total_views": cur["total_views"],
            }

    return {"months": months, "mover_highlight": mover}


def main():
    out = {}
    report = []
    for key, label in CATEGORIES.items():
        result = build_category(key)
        out[key] = result
        if result:
            mh = result["mover_highlight"]
            mh_desc = f"{mh['from_month']}->{mh['to_month']} {mh['mom_change_pct']:+.1f}%" if mh else "n/a"
            report.append(f"{key}({label}): {len(result['months'])}개월, mover={mh_desc}")
        else:
            report.append(f"{key}({label}): 데이터 없음(outputs/silver/silver_{key}_*.jsonl 필요)")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    out["_comment"] = (
        "outputs/silver/silver_{category}_{YYYY-MM}.jsonl 1년치(2025-09~2026-09) 실측 백필 "
        "데이터를 월별로 집계한 것 - 합성 아님. 실시간 대시보드가 밋밋해 보이는 건 "
        "fact_video_snapshot의 UNIQUE(video_id, collected_date) 제약 때문에 하루 여러 번 "
        "수집해도 스냅샷이 1건으로 뭉개져서지(2026-09-03 기준 실측 96%가 스냅샷 1개), 실측 "
        "데이터 자체에 변화가 없는 게 아니다 - 그걸 보여주려고 이미 모아둔 1년치를 재생한다. "
        "mover_highlight는 표본 5건 미만 달을 제외하고 인접한 두 달 중 총 조회수 변화율(%)이 "
        "가장 큰 구간(절대 레벨이 아니라 '가장 극적으로 움직인 구간' 프레이밍)."
    )
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n".join(report))
    print(f"-> {OUT_PATH}")
    print("DONE")


if __name__ == "__main__":
    main()
