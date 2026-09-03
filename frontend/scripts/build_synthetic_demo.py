#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spec.md 분석 5(성장 곡선 유형화)/분석 6(성과 판단 시점) 데모용 스크립트.

**주의: 이 스크립트는 실제 수집 데이터를 쓰지 않는다.** 두 분석 다 "한 영상을 여러
시점에 걸쳐 추적한 시계열"이 있어야 되는데, 지금 실제로 수집된 데이터는 96%가
스냅샷 1개뿐이라(2026-09-03 기준 실측) 통계적으로 의미 있는 결론을 못 낸다.
그래서 "데이터가 쌓이면 이런 분석을 할 수 있다"는 것만 합성(synthetic) 데이터로
시연한다 - 프론트엔드에도 "합성 데이터 데모"라고 명확히 표시한다(실제 결론으로
오인되면 안 됨).

1) 성장 곡선 유형화 (분석 5): 세 가지 원형(급등후급락/완만한롱테일/지연폭발) 곡선에
   노이즈를 섞어 영상 150개(원형당 50개)의 30일치 조회수 곡선을 생성한 뒤, 원형
   라벨을 안 알려주고 K-means(k=3)로 다시 3개 군집을 찾아낸다 - "시계열만 있으면
   이렇게 유형을 나눌 수 있다"는 기법 시연.
2) 성과 판단 시점 (분석 6): 카테고리마다 노이즈 크기를 다르게 줘서 T+24h 조회수가
   T+30d 조회수를 얼마나 잘 예측하는지(R^2) 합성 생성 - spec.md 예시("엔터테인먼트
   R^2=0.82, 교육 R^2=0.41")와 같은 형태의 결과를 만들어 봄.

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
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"-> {OUT_PATH}")
    print("DONE (synthetic demo data)")


if __name__ == "__main__":
    main()
