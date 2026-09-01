(function () {
  const M = window.MOCK;

  const state = { category: "all", segment: "all" };

  // ---------- helpers ----------
  const fmt = (n) => Math.round(n).toLocaleString("ko-KR");
  const fmt1 = (n) => (Math.round(n * 10) / 10).toLocaleString("ko-KR");

  function formatDateTime(iso) {
    const d = new Date(iso);
    const pad = (x) => String(x).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function categoryColor(id) {
    const c = M.categories.find((c) => c.id === id);
    return c ? c.color : "#999";
  }
  function categoryLabel(id) {
    const c = M.categories.find((c) => c.id === id);
    return c ? c.label : id;
  }
  function activeCategoryIds() {
    return state.category === "all" ? M.categories.map((c) => c.id) : [state.category];
  }
  function deltaSpan(pct) {
    const up = pct >= 0;
    return `<span class="kpi-delta ${up ? "up" : "down"}">${up ? "▲" : "▼"} 지난 기간 대비 ${fmt1(Math.abs(pct))}%</span>`;
  }

  // ---------- tabs ----------
  function initTabs() {
    const buttons = document.querySelectorAll(".tab-btn");
    buttons.forEach((btn) => {
      btn.addEventListener("click", () => {
        buttons.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        document.getElementById(`panel-${btn.dataset.tab}`).classList.add("active");
      });
    });
  }

  // ---------- category filter chips (전체 탭 공통) ----------
  function renderChips() {
    const el = document.getElementById("categoryChips");
    el.innerHTML = M.filterCategories
      .map((c) => `<button class="chip-btn ${c.id === state.category ? "active" : ""}" data-cat="${c.id}">${c.label}</button>`)
      .join("");
    el.querySelectorAll(".chip-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.category = btn.dataset.cat;
        renderChips();
        renderAllCategoryDependent();
      });
    });
  }

  // ---------- header freshness ----------
  function renderFreshness() {
    const el = document.getElementById("freshness");
    el.innerHTML = `마지막 배치 갱신: <strong>${formatDateTime(M.meta.lastBatchAt)}</strong><br/>${M.meta.batchFrequency}`;
  }

  // ---------- 홈 ----------
  function renderKpiRow() {
    const el = document.getElementById("kpiRow");
    const items = [
      { label: "분석 대상 영상 수", value: fmt(M.meta.totalVideos) + "건", sub: M.meta.dataWindow },
      { label: "분석 카테고리 수", value: M.meta.totalCategories + "개", sub: "인물·블로그 제외" },
      { label: "데이터 갱신 주기", value: "1일 3회", sub: "배치 스냅샷 기반" },
    ];
    el.innerHTML = items
      .map((i) => `<div class="kpi-card"><p class="kpi-label">${i.label}</p><p class="kpi-value">${i.value}</p><p class="kpi-sub">${i.sub}</p></div>`)
      .join("");
  }

  function renderWeeklyBanner() {
    const el = document.getElementById("weeklyBanner");
    const cat = state.category === "all" ? "game" : state.category;
    const text = M.weeklyBanner[cat];
    const label = state.category === "all" ? `예시: ${categoryLabel(cat)} 카테고리 기준 (상단에서 카테고리를 고르면 바뀌어요)` : `이번 주 ${categoryLabel(cat)} 카테고리 업로드 추천 타이밍`;
    el.innerHTML = `
      <div><strong>${label}</strong><br/>${text}</div>
      <span class="banner-cta" data-jump="upload">자세히 보기 →</span>
    `;
    el.querySelector(".banner-cta").addEventListener("click", () => {
      document.querySelector('.tab-btn[data-tab="upload"]').click();
    });
  }

  function renderTrendingGrid() {
    const el = document.getElementById("trendingGrid");
    const ids = activeCategoryIds();
    const items = M.rankTrend
      .filter((r) => r.trendDirection === "up" && ids.includes(r.categoryId))
      .sort((a, b) => b.rankChange - a.rankChange)
      .slice(0, 3);
    el.innerHTML = items
      .map((r) => {
        const growthLabel = r.viewGrowthPct >= 0 ? `+${r.viewGrowthPct}%` : `${r.viewGrowthPct}%`;
        return `
        <div class="video-card">
          <div class="video-thumb">
            ▶
            <span class="thumb-badge rising">급상승 ▲${r.rankChange}</span>
          </div>
          <div class="video-body">
            <p class="video-title">${r.title}</p>
            <p class="video-meta">${r.channelName} · 구독자 ${r.subscribers}</p>
            <p class="video-meta">조회수 ${fmt(r.views)} · 좋아요 ${fmt(r.likes)}</p>
            <div class="video-insight">게시 ${r.postedDaysAgo}일 만에 최초 ${r.firstRank}위 → 최신 ${r.latestRank}위, 조회수 ${growthLabel}</div>
          </div>
        </div>`;
      })
      .join("") || `<p class="note">이 카테고리는 최근 급상승 영상이 없어요.</p>`;
  }

  function renderSteadyGrid() {
    const el = document.getElementById("steadyGrid");
    const ids = activeCategoryIds();
    const items = M.steadySellers.filter((s) => ids.includes(s.categoryId)).slice(0, 3);
    el.innerHTML = items
      .map(
        (s) => `
      <div class="video-card">
        <div class="video-thumb">
          ▶
          <span class="thumb-badge steady">스테디셀러</span>
        </div>
        <div class="video-body">
          <p class="video-title">${s.title}</p>
          <p class="video-meta">${s.channelName} · 게시 ${s.postedDaysAgo}일 전</p>
          <p class="video-meta">누적 조회수 ${fmt(s.cumulativeViews)} · 좋아요 ${fmt(s.likes)}</p>
          <div class="video-insight">${s.insightTag}</div>
        </div>
      </div>`
      )
      .join("") || `<p class="note">이 카테고리는 스테디셀러 데이터가 아직 없어요.</p>`;
  }

  function renderHomeCategorySummary() {
    const el = document.getElementById("homeCategorySummary");
    const maxViews = Math.max(...M.categoryBenchmark.map((c) => c.avgViews));
    el.innerHTML = M.categoryBenchmark
      .map((c) => {
        const pct = Math.round((c.avgViews / maxViews) * 100);
        return `
        <div class="mini-bar-row">
          <div class="row-label"><span>${c.categoryLabel}</span><span>평균 ${fmt(c.avgViews)}회</span></div>
          <div class="mini-bar-track"><div class="mini-bar-fill" style="width:${pct}%;background:${categoryColor(c.categoryId)}"></div></div>
        </div>`;
      })
      .join("");
    document.getElementById("excludedNote").textContent = M.meta.excludedCategoryNote;
  }

  // ---------- 추천 채널 ----------
  function renderChannels() {
    const el = document.getElementById("channelGrid");
    let list;
    if (state.category === "all") {
      list = M.categories.map((c) => ({ ...M.recommendedChannels[c.id][0], categoryId: c.id }));
    } else {
      list = M.recommendedChannels[state.category].map((c) => ({ ...c, categoryId: state.category }));
    }
    el.innerHTML = list
      .map((c) => {
        const initial = c.name.charAt(0);
        return `
        <div class="channel-card">
          <div class="channel-top">
            <div class="channel-icon" style="background:${categoryColor(c.categoryId)}">${initial}</div>
            <div>
              <p class="channel-name">${c.name}</p>
              <p class="channel-sub">${categoryLabel(c.categoryId)} · 구독자 ${c.subscribers}</p>
            </div>
          </div>
          <span class="channel-reason">★ ${c.reasonTag}</span>
          <p class="channel-desc">${c.description}</p>
          <p class="channel-topvideo">대표 영상: "${c.topVideo}" · 누적 조회수 ${c.topVideoViews}만 회</p>
        </div>`;
      })
      .join("");
  }

  function renderChannelTip() {
    document.getElementById("channelTip").textContent = M.channelTip;
  }

  // ---------- 업로드 가이드 ----------
  function initSegmentFilter() {
    const sel = document.getElementById("segmentFilter");
    sel.innerHTML = M.subscriberSegments.map((s) => `<option value="${s.id}">${s.label}</option>`).join("");
    sel.addEventListener("change", () => {
      state.segment = sel.value;
      renderHeatmap();
    });
  }

  function getHeatmapSource() {
    const ids = activeCategoryIds();
    const days = M.uploadDayBuckets.length;
    const times = M.uploadTimeBuckets.length;
    const base = Array.from({ length: days }, () => Array(times).fill(0));
    const sample = Array.from({ length: days }, () => Array(times).fill(0));
    ids.forEach((id) => {
      const src = M.uploadHeatmap[id];
      for (let d = 0; d < days; d++) {
        for (let t = 0; t < times; t++) {
          base[d][t] += src.base[d][t] / ids.length;
          sample[d][t] += src.sample[d][t];
        }
      }
    });
    // bestCell: 단일 카테고리면 그대로, 전체면 최댓값 셀 계산
    let bestCell = ids.length === 1 ? M.uploadHeatmap[ids[0]].bestCell : null;
    if (!bestCell) {
      let max = -1;
      for (let d = 0; d < days; d++)
        for (let t = 0; t < times; t++)
          if (base[d][t] > max) { max = base[d][t]; bestCell = [d, t]; }
    }
    return { base, sample, bestCell };
  }

  function renderHeatmap() {
    const { base, sample, bestCell } = getHeatmapSource();
    const factor = M.subscriberSegments.find((s) => s.id === state.segment).factor;
    const maxVal = Math.max(...base.flat()) * (factor / M.subscriberSegments[0].factor || 1) || 1;

    let html = `<table class="heatmap-table"><thead><tr><th>요일 \\ 시간대</th>${M.uploadTimeBuckets.map((t) => `<th>${t}</th>`).join("")}</tr></thead><tbody>`;
    M.uploadDayBuckets.forEach((day, d) => {
      html += `<tr><th>${day}</th>`;
      M.uploadTimeBuckets.forEach((time, t) => {
        const rawVal = base[d][t] * factor;
        const sampleCount = Math.round(sample[d][t] * (state.segment === "all" ? 1 : 0.4));
        const intensity = Math.min(1, rawVal / (maxVal || 1));
        const bg = `rgba(76, 63, 240, ${0.1 + intensity * 0.55})`;
        const isBest = bestCell && bestCell[0] === d && bestCell[1] === t;
        const isRecommended = sampleCount >= 30;
        html += `
          <td class="heatmap-cell ${isBest ? "best-cell" : ""}" style="background:${bg}" title="표본 영상 수: ${sampleCount}건">
            <div class="cell-value">${fmt(rawVal)}${isBest ? '<span class="best-star">★</span>' : ""}</div>
            <div class="cell-sample">표본 ${sampleCount}건 ${isRecommended ? '<span class="rec-badge">추천</span>' : ""}</div>
          </td>`;
      });
      html += `</tr>`;
    });
    html += `</tbody></table>`;
    document.getElementById("heatmapWrap").innerHTML = html;
  }

  function renderWeeklyChecklist() {
    const cat = state.category === "all" ? "game" : state.category;
    const el = document.getElementById("weeklyChecklist");
    el.innerHTML = M.weeklyChecklist[cat].map((item) => `<li>${item}</li>`).join("");
  }

  function renderUploadTip() {
    document.getElementById("uploadTip").textContent = M.uploadTip;
  }

  // ---------- 카테고리 트렌드 ----------
  function getCategoryAgg() {
    const rows = state.category === "all" ? M.categoryBenchmark : M.categoryBenchmark.filter((c) => c.categoryId === state.category);
    const totalVideos = rows.reduce((s, r) => s + r.videoCount, 0);
    const wAvg = (key) => rows.reduce((s, r) => s + r[key] * r.videoCount, 0) / totalVideos;
    return {
      avgViews: wAvg("avgViews"),
      engagementRatePct: wAvg("engagementRatePct"),
      medianDurationSec: wAvg("medianDurationSec"),
      viewsWowPct: wAvg("viewsWowPct"),
      engagementWowPct: wAvg("engagementWowPct"),
      rows,
    };
  }

  function renderCategoryKpiRow() {
    const agg = getCategoryAgg();
    const el = document.getElementById("categoryKpiRow");
    el.innerHTML = `
      <div class="kpi-card">
        <p class="kpi-label">평균 조회수 (최근 기준)</p>
        <p class="kpi-value">${fmt(agg.avgViews)}회</p>
        ${deltaSpan(agg.viewsWowPct)}
      </div>
      <div class="kpi-card">
        <p class="kpi-label">평균 참여율 (좋아요+댓글/조회수) <span class="kpi-status">gold 집계 예정</span></p>
        <p class="kpi-value">${fmt1(agg.engagementRatePct)}%</p>
        ${deltaSpan(agg.engagementWowPct)}
      </div>
      <div class="kpi-card">
        <p class="kpi-label">평균 영상 길이</p>
        <p class="kpi-value">${Math.floor(agg.medianDurationSec / 60)}분 ${Math.round(agg.medianDurationSec % 60)}초</p>
        <p class="kpi-sub">5~10분 영상이 가장 많아요</p>
      </div>
    `;
  }

  function renderDurationDist() {
    const agg = getCategoryAgg();
    const buckets = agg.rows[0].durationDistribution.map((d) => d.bucket);
    const totalVideos = agg.rows.reduce((s, r) => s + r.videoCount, 0);
    const el = document.getElementById("durationDist");
    el.innerHTML = buckets
      .map((bucket, i) => {
        const pct = agg.rows.reduce((s, r) => s + r.durationDistribution[i].pct * r.videoCount, 0) / totalVideos;
        return `
        <div class="dist-row">
          <span>${bucket}</span>
          <div class="dist-track"><div class="dist-fill" style="width:${pct}%"></div></div>
          <span class="dist-value">${Math.round(pct)}%</span>
        </div>`;
      })
      .join("");
  }

  function renderSubscriberComparison() {
    const agg = getCategoryAgg();
    const segments = agg.rows[0].subscriberComparison.map((s) => s.segment);
    const totalVideos = agg.rows.reduce((s, r) => s + r.videoCount, 0);
    const maxMult = Math.max(...agg.rows.flatMap((r) => r.subscriberComparison.map((s) => s.multiplier)));
    const el = document.getElementById("subscriberComparison");
    el.innerHTML = segments
      .map((seg, i) => {
        const mult = agg.rows.reduce((s, r) => s + r.subscriberComparison[i].multiplier * r.videoCount, 0) / totalVideos;
        const pct = (mult / maxMult) * 100;
        return `
        <div class="dist-row">
          <span>${seg}</span>
          <div class="dist-track"><div class="dist-fill" style="width:${pct}%"></div></div>
          <span class="dist-value">${fmt1(mult)}배</span>
        </div>`;
      })
      .join("");
    document.getElementById("subscriberInsight").textContent =
      "구독자가 적어도 구독자 대비 조회수 비율은 오히려 더 좋을 수 있어요. 업로드 시간대와 영상 길이만 잘 맞춰도 구독자 규모와 상관없이 좋은 반응을 얻을 수 있는 여지가 있다는 뜻이에요.";
  }

  function renderCategoryBarChart() {
    const el = document.getElementById("categoryBarChart");
    const maxViews = Math.max(...M.categoryBenchmark.map((c) => c.avgViews));
    el.innerHTML = M.categoryBenchmark
      .map((c) => {
        const pct = Math.round((c.avgViews / maxViews) * 100);
        const dim = state.category !== "all" && state.category !== c.categoryId ? "opacity:0.35;" : "";
        return `
        <div class="bar-row" style="${dim}">
          <span>${c.categoryLabel}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${categoryColor(c.categoryId)}"></div></div>
          <span class="bar-value">${fmt(c.avgViews)}</span>
        </div>`;
      })
      .join("");
  }

  function renderCategoryTable() {
    const tbody = document.querySelector("#categoryTable tbody");
    tbody.innerHTML = M.categoryBenchmark
      .map(
        (c) => `
      <tr>
        <td>${c.categoryLabel}</td><td>${fmt(c.videoCount)}</td><td>${fmt(c.avgViews)}</td><td>${fmt(c.avgLikes)}</td>
        <td>${fmt(c.avgViewsPerDay)}</td><td>${Math.round(c.medianDurationSec / 60)}분</td><td>${c.topUploadHour}시</td>
      </tr>`
      )
      .join("");
  }

  // ---------- orchestration ----------
  function renderAllCategoryDependent() {
    renderWeeklyBanner();
    renderTrendingGrid();
    renderSteadyGrid();
    renderChannels();
    renderHeatmap();
    renderWeeklyChecklist();
    renderCategoryKpiRow();
    renderDurationDist();
    renderSubscriberComparison();
    renderCategoryBarChart();
  }

  function init() {
    initTabs();
    renderChips();
    renderFreshness();
    renderKpiRow();
    renderHomeCategorySummary();
    renderChannelTip();
    initSegmentFilter();
    renderUploadTip();
    renderCategoryTable();
    renderAllCategoryDependent();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
