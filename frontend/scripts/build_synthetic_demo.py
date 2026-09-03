#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
"수집은 가능한데 아직 안 쌓여서 못 하는 분석"을 더미(합성) 데이터로 시연하는 스크립트.
spec.md 분석 5(성장 곡선 유형화)/분석 6(성과 판단 시점) + 채널 성장 속도 + 주간
재집계 트렌드, 총 4개.

**주의: 이 스크립트는 실제 수집 데이터를 쓰지 않는다.** 넷 다 "여러 시점에 걸쳐
추적한 시계열"이 있어야 되는데, 지금은 그 시계열이 쌓일 시간이 아직 안 지났을 뿐
(파이프라인이 collect할 수 있는 필드/API 호출 자체는 이미 다 있음 - 데이터가
없어서 못 하는 게 아니라 아직 안 쌓여서 못 하는 것). 그래서 "데이터가 쌓이면 이런
분석을 할 수 있다"는 것만 합성(synthetic) 데이터로 시연한다 - 프론트엔드에도
"합성 데이터 데모"라고 명확히 표시한다(실제 결론으로 오인되면 안 됨).

1) 성장 곡선 유형화 (분석 5): 세 가지 원형(급등후급락/완만한롱테일/지연폭발) 곡선에
   노이즈를 섞어 영상 150개(원형당 50개)의 30일치 조회수 곡선을 생성한 뒤, 원형
   라벨을 안 알려주고 K-means(k=3)로 다시 3개 군집을 찾아낸다 - "시계열만 있으면
   이렇게 유형을 나눌 수 있다"는 기법 시연. (필요 데이터: fact_video_stats 다중 스냅샷)
2) 성과 판단 시점 (분석 6): 카테고리마다 노이즈 크기를 다르게 줘서 T+24h 조회수가
   T+30d 조회수를 얼마나 잘 예측하는지(R^2) 합성 생성. (필요 데이터: 동일)
3) 채널 성장 속도: dim_channel은 이미 API 호출은 구현돼 있지만 아직 하루치
   스냅샷만 있어서 "성장 곡선"을 못 그림 - 3가지 성장 유형(꾸준한 성장/초반 급성장
   후 정체/뒤늦은 성장)의 180일 구독자 추이를 합성해서 시연. (필요 데이터:
   dim_channel 일 스냅샷 SCD Type 2 누적)
4) 주간 재집계 트렌드: 지금 "카테고리 트렌드" 탭의 "지난 기간 대비" 배지가
   nodata인 이유가 바로 이거 - Gold가 이번이 첫 집계라 비교할 지난 주가 없음.
   10주치 카테고리별 평균 조회수 추이를 합성해서, 매주 재계산이 쌓이면 어떤
   화면이 되는지 시연. (필요 데이터: Gold 테이블 주 1회 재계산 이력)

실행: repo 루트 어디서든 `python frontend/scripts/build_synthetic_demo.py`
"""
import json
import os

import numpy as np
from sklearn.cluster import KMeans

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.dirname(SCRIPT_DIR)
OUT_PATH = f"{FRONTEND_DIR}/mock/synthetic_demo.json"

RNG = np.random.default_rng(42)
DAYS = np.arange(1, 31)  # T+1일 ~ T+30일


# ============================================================
# 1) 성장 곡선 유형화 (분석 5)
# ============================================================
def curve_spike_crash(days, scale):
    """급등 후 급락 - 초반 며칠에 몰아서 터지고 이후 거의 안 늘어남"""
    return scale * (1 - np.exp(-days / 1.5)) * np.exp(-days / 25) + scale * 0.15 * (1 - np.exp(-days / 1.5))


def curve_long_tail(days, scale):
    """완만한 롱테일 - 꾸준히 조금씩 계속 늘어남 (정보/튜토리얼형)"""
    return scale * 0.4 * np.log1p(days) / np.log1p(30)


def curve_delayed_burst(days, scale):
    """지연 폭발 - 초반엔 거의 안 뜨다가 뒤늦게 알고리즘에 픽업됨"""
    return scale * (1 / (1 + np.exp(-(days - 20) / 3)))


ARCHETYPES = {
    "spike_crash": (curve_spike_crash, "급등 후 급락"),
    "long_tail": (curve_long_tail, "완만한 롱테일"),
    "delayed_burst": (curve_delayed_burst, "지연 폭발"),
}


def build_growth_curve_clusters():
    n_per_type = 50
    all_curves, true_labels = [], []
    for key, (fn, _) in ARCHETYPES.items():
        for _ in range(n_per_type):
            scale = RNG.uniform(0.6, 1.4)
            noise = RNG.normal(0, 0.04, size=len(DAYS))
            curve = np.clip(fn(DAYS, scale) + noise, 0, None)
            all_curves.append(curve)
            true_labels.append(key)
    X = np.array(all_curves)
    # 절대 크기(조회수 규모) 말고 "모양"으로 군집화 - 각 곡선을 자기 최댓값으로 정규화
    X_norm = X / (X.max(axis=1, keepdims=True) + 1e-9)

    km = KMeans(n_clusters=3, n_init=10, random_state=42).fit(X_norm)
    pred_labels = km.labels_

    # K-means가 찾은 군집 번호(0/1/2)는 순서가 무작위라, 실제 원형과 최대로 겹치는
    # 쪽으로 이름을 붙여준다 (군집 번호 자체엔 의미가 없으므로 - Hungarian 없이
    # 그리디로 매칭해도 3개뿐이라 충분).
    label_names = list(ARCHETYPES.keys())
    cluster_to_name = {}
    used = set()
    for c in range(3):
        idx = [i for i, p in enumerate(pred_labels) if p == c]
        counts = {name: sum(1 for i in idx if true_labels[i] == name) for name in label_names}
        best = max((n for n in label_names if n not in used), key=lambda n: counts[n])
        cluster_to_name[c] = best
        used.add(best)

    accuracy = sum(
        1 for i in range(len(pred_labels)) if cluster_to_name[pred_labels[i]] == true_labels[i]
    ) / len(pred_labels)

    centroids = []
    for c in range(3):
        name = cluster_to_name[c]
        _, label_ko = ARCHETYPES[name]
        centroids.append({
            "cluster_key": name,
            "label": label_ko,
            "sample_count": int(sum(1 for p in pred_labels if p == c)),
            # 화면엔 정규화된(0~1) 모양만 보여줌 - 절대 조회수가 아니라 "패턴"이 핵심이라서
            "curve": [round(float(v), 4) for v in km.cluster_centers_[c]],
        })

    return {
        "days": DAYS.tolist(),
        "clusters": centroids,
        "recovery_accuracy_pct": round(accuracy * 100, 1),
        "note": "K-means가 원형 라벨을 모른 채 순수 곡선 모양만으로 군집을 나눈 뒤, "
                f"원래 생성할 때 쓴 라벨과 비교해보니 {round(accuracy * 100, 1)}% 일치했습니다.",
    }


# ============================================================
# 2) 성과 판단 시점 (분석 6)
# ============================================================
def build_judgment_timing():
    # 카테고리마다 "24시간 성과가 최종 성과를 얼마나 잘 예측하는지"의 노이즈 크기를
    # 다르게 줘서, spec.md 예시(엔터테인먼트처럼 24시간 안에 판가름나는 카테고리 vs
    # 교육처럼 나중에 재발견되는 카테고리가 섞여 있는 상황)를 흉내낸다.
    categories = [
        ("entertainment_like", "엔터테인먼트형", 0.12),
        ("gaming_like", "게임형", 0.28),
        ("education_like", "교육형", 0.55),
    ]
    out = []
    for key, label, noise_scale in categories:
        n = 200
        true_final = RNG.lognormal(mean=9, sigma=1.0, size=n)
        t24 = true_final * RNG.uniform(0.2, 0.5, size=n) * np.exp(RNG.normal(0, noise_scale, size=n))
        t30d = true_final * np.exp(RNG.normal(0, noise_scale * 0.6, size=n))

        log_t24 = np.log(t24)
        log_t30d = np.log(t30d)
        corr = np.corrcoef(log_t24, log_t30d)[0, 1]
        r_squared = corr ** 2

        out.append({
            "category_key": key,
            "label": label,
            "sample_size": n,
            "r_squared": round(float(r_squared), 2),
        })
    out.sort(key=lambda r: r["r_squared"], reverse=True)
    return {
        "results": out,
        "note": "R^2이 높을수록(1에 가까울수록) T+24시간 조회수만 보고도 최종 성과를 "
                "예측할 수 있다는 뜻이고, 낮을수록 더 오래(예: T+30일) 지켜봐야 판단이 정확해집니다.",
    }


# ============================================================
# 3) 채널 성장 속도 (spec.md 3-3 dim_channel 일 스냅샷 - "왜 필요한가"의 근거를
#    직접 보여주는 데모: 구독자 수는 지금도 수집하지만 스냅샷이 하루 1개뿐이라
#    아직 "성장 속도"를 볼 수 없음. 앞으로 dim_channel이 매일 쌓이면 이렇게
#    구독자 증가 곡선을 유형별로 비교할 수 있다는 것을 시연)
# ============================================================
def curve_steady_grower(days, start, end):
    """꾸준한 성장 - 거의 직선에 가깝게 계속 늘어남"""
    return start + (end - start) * (days / days[-1])


def curve_viral_then_plateau(days, start, end):
    """초반 급성장 후 정체 - 바이럴 영상 한 번으로 확 늘고 그 뒤론 완만"""
    t = days / days[-1]
    return start + (end - start) * (1 - np.exp(-t * 5))


def curve_late_bloomer(days, start, end):
    """뒤늦은 성장 - 초반엔 거의 그대로다가 후반에 갑자기 늘어남"""
    t = days / days[-1]
    return start + (end - start) * (1 / (1 + np.exp(-(t - 0.7) * 10)))


CHANNEL_ARCHETYPES = [
    ("steady_grower", "꾸준한 성장형", curve_steady_grower, 8_000, 42_000),
    ("viral_then_plateau", "초반 급성장 후 정체형", curve_viral_then_plateau, 3_000, 65_000),
    ("late_bloomer", "뒤늦은 성장형", curve_late_bloomer, 15_000, 30_000),
]
CHANNEL_GROWTH_DAYS = np.arange(1, 181)  # 최근 180일


def build_channel_growth():
    channels = []
    for key, label, fn, start, end in CHANNEL_ARCHETYPES:
        noise = RNG.normal(0, (end - start) * 0.01, size=len(CHANNEL_GROWTH_DAYS))
        raw = fn(CHANNEL_GROWTH_DAYS, start, end) + np.cumsum(noise) * 0.05
        # 구독자 수는 거의 감소하지 않으므로 단조 비감소로 정리
        monotonic = np.maximum.accumulate(raw)
        norm = monotonic / monotonic.max()
        channels.append({
            "channel_key": key,
            "label": label,
            "start_subscribers": int(monotonic[0]),
            "end_subscribers": int(monotonic[-1]),
            "curve": [round(float(v), 4) for v in norm],
        })
    return {
        "days": CHANNEL_GROWTH_DAYS.tolist(),
        "channels": channels,
        "note": (
            "spec.md dim_channel 설계상 구독자 수는 '일 1회 스냅샷'으로 매일 쌓아야 하는데, "
            "아직 하루치(스냅샷 1개)만 있어서 실제로는 이 곡선을 못 그립니다. "
            "dim_channel이 매일 누적되면 채널마다 성장 패턴(꾸준한 성장/초반 급성장 후 정체/"
            "뒤늦은 성장)을 이렇게 구분해 볼 수 있다는 것을 곡선 모양으로 시연한 것입니다."
        ),
    }


# ============================================================
# 4) 주간 재집계 트렌드 (spec.md fact_hourly_supply/Gold "주 1회 재계산" 및
#    프론트 "지난 기간 대비" 배지가 지금 nodata인 이유를 직접 보여주는 데모:
#    Gold 파이프라인이 이번 주에 처음 돌아서 비교할 지난 주 스냅샷이 아직 없음.
#    매주 쌓이면 이렇게 주간 추이를 볼 수 있다는 것을 시연)
# ============================================================
def build_weekly_trend():
    categories = [
        ("gaming", "게임", 1400, 0.015),
        ("autos_vehicles", "자동차·차량", 900, -0.01),
        ("film_animation", "영화·애니메이션", 1100, 0.005),
    ]
    n_weeks = 10
    weeks = list(range(1, n_weeks + 1))
    out = []
    for key, label, base, weekly_drift in categories:
        series = [base]
        for _ in range(n_weeks - 1):
            drift = 1 + weekly_drift + RNG.normal(0, 0.05)
            series.append(max(1.0, series[-1] * drift))
        wow_change_pct = round((series[-1] - series[-2]) / series[-2] * 100, 1)
        norm = np.array(series) / max(series)
        out.append({
            "category_key": key,
            "label": label,
            "curve": [round(float(v), 4) for v in norm],
            "latest_avg_views_per_day": round(series[-1], 1),
            "wow_change_pct": wow_change_pct,
        })
    return {
        "weeks": weeks,
        "categories": out,
        "note": (
            "지금 '카테고리 트렌드' 탭의 '지난 기간 대비' 배지가 nodata인 건 Gold 파이프라인이 "
            "이번 주에 처음 돌아서 비교할 지난 주 스냅샷이 아직 없기 때문입니다. "
            "매주 재계산이 쌓이면 이렇게 카테고리별 주간 추이와 전주 대비 증감(%)을 "
            "볼 수 있게 된다는 것을 시연한 것입니다."
        ),
    }


def main():
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    out = {
        "_is_synthetic": True,
        "_comment": (
            "이 파일은 실제 수집 데이터가 아니라 합성(synthetic) 데이터입니다. "
            "실제로는 한 영상을 여러 시점에 걸쳐 추적한 시계열이 충분히 쌓여야 "
            "(spec.md의 적응형 수집 주기 참고) 이 분석들을 진짜로 할 수 있는데, "
            "2026-09-03 기준 실측 데이터의 96%가 스냅샷 1개뿐이라 아직 불가능합니다. "
            "데이터 엔지니어링 파이프라인이 이런 종류의 분석까지 지원할 수 있다는 것을 "
            "보여주기 위한 기법 시연(demo)입니다."
        ),
        "growth_curve_clusters": build_growth_curve_clusters(),
        "judgment_timing": build_judgment_timing(),
        "channel_growth": build_channel_growth(),
        "weekly_trend": build_weekly_trend(),
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"-> {OUT_PATH}")
    print("DONE (synthetic demo data)")


if __name__ == "__main__":
    main()
