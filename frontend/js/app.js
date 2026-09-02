/**
 * app.js
 * ------
 * 화면 조립 담당. 데이터를 불러와서(data.js) 스코어링하고(recommend.js),
 * 카테고리 전환/탭 전환 등 상호작용을 연결합니다.
 */
(async function () {
  const cfg = window.APP_CONFIG;
  let currentCategory = cfg.CATEGORIES[0].key;
  const DATA = {};

  const fmtInt = (n) => Math.round(n).toLocaleString("ko-KR");
  const fmtPct = (n) => (n * 100).toFixed(1) + "%";
  const fmtDuration = Charts.fmtDuration;
  const categoryInfo = (key) => cfg.CATEGORIES.find((c) => c.key === key) || {};

  function svgPlayIcon() {
    return `<svg class="play-icon" width="34" height="34" viewBox="0 0 24 24" fill="rgba(255,255,255,0.92)"><path d="M8 5v14l11-7z"></path></svg>`;
  }

  function setMetaBadge(meta) {
    const badge = document.getElementById("meta-badge");
    badge.textContent = `${cfg.USE_MOCK ? "샘플(mock) 데이터" : "실시간 데이터"} · 마지막 업데이트 ${meta.last_updated.slice(0, 10)}`;
  }

  function renderCategoryChips() {
    const row = document.getElementById("category-row");
    row.innerHTML = "";
    cfg.CATEGORIES.forEach((c) => {
      const btn = document.createElement("button");
      btn.className = "category-chip" + (c.key === currentCategory ? " active" : "");
      btn.textContent = `${c.emoji} ${c.label}`;
      btn.addEventListener("click", () => {
        currentCategory = c.key;
        [...row.children].forEach((el) => el.classList.remove("active"));
        btn.classList.add("active");
        renderActiveTab();
      });
      row.appendChild(btn);
    });
  }

  /* ---------------- 홈 ---------------- */
  function videoCard(v, kind) {
    const isTrending = kind === "trending";
    const grad = isTrending
      ? "linear-gradient(135deg, #1baf7a, #12805a)"
      : "linear-gradient(135deg, #2a78d6, #1c5aa8)";
    const tag = isTrending ? "🔥 급상승" : "🌱 스테디셀러";
    const reasonClass = isTrending ? "trending" : "steady";
    const subLine = isTrending
      ? `${v.channel_name} · 구독자 ${Recommend.fmtManwon(v.subscriber_count)}`
      : `${v.channel_name} · 게시 ${v.days_since_published}일 전`;
    const statsLine = isTrending
      ? `<span>조회수 <strong>${Recommend.fmtManwon(v.view_count)}</strong></span><span>좋아요 <strong>${Recommend.fmtManwon(v.like_count)}</strong></span>`
      : `<span>누적 조회수 <strong>${Recommend.fmtManwon(v.view_count)}</strong></span><span>좋아요 <strong>${Recommend.fmtManwon(v.like_count)}</strong></span>`;

    const el = document.createElement("div");
    el.className = "vcard";
    el.innerHTML = `
      <a class="thumb-link" href="${v.url}" target="_blank" rel="noopener">
        <div class="thumb" style="background:${grad};">
          ${svgPlayIcon()}
          <span class="tag-badge">${tag}</span>
          <span class="dur-badge">${fmtDuration(v.duration_sec)}</span>
        </div>
      </a>
      <div class="vcard-body">
        <a class="vcard-title" href="${v.url}" target="_blank" rel="noopener">${v.title}</a>
        <div class="vcard-channel"><a href="${v.channelUrl}" target="_blank" rel="noopener">${subLine}</a></div>
        <div class="vcard-stats">${statsLine}</div>
        <div class="vcard-reason ${reasonClass}">${Recommend.explainVideo(v)}</div>
      </div>
    `;
    return el;
  }

  function renderHome() {
    const catInfo = categoryInfo(currentCategory);
    const pool = DATA.videoPool[currentCategory];
    const scored = Recommend.scoreVideoPool(pool);
    const trending = Recommend.pickTrending(scored, 3);
    const steady = Recommend.pickSteady(scored, 3);
    const heatCells = DATA.uploadHeatmap[currentCategory].cells;
    const tip = Recommend.uploadTip(heatCells);

    const callout = document.getElementById("home-tip-callout");
    callout.innerHTML = `
      <div class="tip-callout-icon">💡</div>
      <div style="flex:1;">
        <div class="tip-callout-title">이번 주 ${catInfo.label} 카테고리 업로드 추천 타이밍</div>
        <div class="tip-callout-body">${tip.dayLabel} ${tip.slotLabel}에 올린 영상들이 조회수가 가장 잘 나왔어요. ${tip.text}</div>
      </div>
    `;

    const trendingGrid = document.getElementById("trending-grid");
    trendingGrid.innerHTML = "";
    trending.forEach((v) => trendingGrid.appendChild(videoCard(v, "trending")));

    const steadyGrid = document.getElementById("steady-grid");
    steadyGrid.innerHTML = "";
    steady.forEach((v) => steadyGrid.appendChild(videoCard(v, "steady")));
  }

  /* ---------------- 추천 채널 ---------------- */
  const AVATAR_GRADIENTS = [
    "linear-gradient(135deg, #1baf7a, #12805a)",
    "linear-gradient(135deg, #2a78d6, #1c5aa8)",
    "linear-gradient(135deg, #eb6834, #b84e22)"
  ];

  function channelCard(c, idx) {
    const initial = c.name.slice(0, 1);
    const el = document.createElement("div");
    el.className = "chcard";
    el.innerHTML = `
      <div class="chcard-head">
        <div class="chcard-avatar" style="background:${AVATAR_GRADIENTS[idx % AVATAR_GRADIENTS.length]};">${initial}</div>
        <div>
          <div class="chcard-name"><a href="${c.url}" target="_blank" rel="noopener">${c.name}</a></div>
          <div class="chcard-subs">구독자 ${Recommend.fmtManwon(c.subscriber_count)}</div>
        </div>
      </div>
      <div class="chcard-highlight">📌 ${Recommend.explainChannel(c)}</div>
      <div class="chcard-desc">
        영상 1건당 평균 조회수 ${Recommend.fmtManwon(c.avg_views_per_video)} · 참여율 ${fmtPct(c.avg_engagement_rate)} · 주 ${c.upload_freq_per_week}회 업로드
      </div>
      <div class="chcard-rule"></div>
      <div class="chcard-rep">대표 영상: <a href="${c.representativeUrl}" target="_blank" rel="noopener">"${c.representative_video.title}"</a> · 조회수 ${Recommend.fmtManwon(c.representative_video.view_count)}</div>
    `;
    return el;
  }

  function renderChannels() {
    const catInfo = categoryInfo(currentCategory);
    document.getElementById("channels-sub").textContent =
      `${catInfo.label} 카테고리에서 업로드 패턴이나 성장 방식이 참고할 만한 채널 3곳을 골라봤어요.`;

    const pool = DATA.channelPool[currentCategory];
    const scored = Recommend.scoreChannelPool(pool);
    const top = Recommend.pickTopChannels(scored, 3);

    const grid = document.getElementById("channel-grid");
    grid.innerHTML = "";
    top.forEach((c, i) => grid.appendChild(channelCard(c, i)));
  }

  /* ---------------- 업로드 가이드 ---------------- */
  function renderGuide() {
    const catInfo = categoryInfo(currentCategory);
    document.getElementById("heatmap-sub").textContent =
      `최근 ${catInfo.label} 카테고리 영상들의 업로드 시점과 조회수를 함께 봤어요. 색이 진할수록 반응이 좋았던 시간대예요.`;

    const cells = DATA.uploadHeatmap[currentCategory].cells;
    Charts.renderHeatmap(document.getElementById("upload-heatmap"), {
      dayLabels: cfg.DAY_GROUPS,
      slotLabels: cfg.TIME_SLOTS,
      cells
    });

    // 요일 그룹별 평균 영상 길이 (히트맵 부가 정보)
    const durationRow = document.getElementById("duration-row");
    durationRow.innerHTML = "";
    cfg.DAY_GROUPS.forEach((dLabel, di) => {
      const dayCells = cells.filter((c) => c.day === di);
      const avg = dayCells.reduce((s, c) => s + c.avg_duration_sec, 0) / dayCells.length;
      const span = document.createElement("span");
      span.innerHTML = `${dLabel} 평균 영상 길이 <strong>${fmtDuration(avg)}</strong>`;
      durationRow.appendChild(span);
    });

    const tip = Recommend.uploadTip(cells);

    const checklist = document.getElementById("checklist");
    checklist.innerHTML = "";
    const items = [
      `${tip.dayLabel} ${tip.slotLabel}에 업로드 예약해보기`,
      `영상 길이는 ${bestDurationBucketLabel()} 사이로 맞춰보기`,
      `썸네일에 핵심 장면이나 텍스트를 눈에 띄게 넣기`,
      `업로드 직후 커뮤니티 탭에 소식 남기기`
    ];
    items.forEach((text) => {
      const row = document.createElement("div");
      row.className = "checklist-item";
      row.innerHTML = `<span class="checklist-box"></span><span>${text}</span>`;
      checklist.appendChild(row);
    });

    document.getElementById("guide-tip-card").innerHTML = `
      <div class="tip-card-title">💬 알아두면 좋은 점</div>
      <div class="tip-card-body">
        토요일·일요일 밤에는 반응이 좋은 만큼 비슷한 콘텐츠를 올리는 채널도 많아요. 평일 저녁은 반응은 조금 낮아도 경쟁이 적어서,
        신규 채널이라면 평일 저녁부터 꾸준히 시도해보는 것도 방법이에요.
      </div>
    `;
  }

  function bestDurationBucketLabel() {
    const trend = DATA.categoryTrend[currentCategory];
    const top = [...trend.duration_distribution].sort((a, b) => b.pct - a.pct)[0];
    return top.label;
  }

  /* ---------------- 카테고리 트렌드 ---------------- */
  function statTile({ label, value, delta }) {
    const tile = document.createElement("div");
    tile.className = "stat-tile";
    const deltaHtml = delta ? `<div class="stat-delta ${delta.good ? "good" : ""}">▲ 지난 기간 대비 ${delta.pct}%</div>` : "";
    tile.innerHTML = `<div class="stat-label">${label}</div><div class="stat-value">${value}</div>${deltaHtml}`;
    return tile;
  }

  function renderTrend() {
    const trend = DATA.categoryTrend[currentCategory];

    const grid = document.getElementById("trend-stats");
    grid.innerHTML = "";
    const viewsDeltaPct = (((trend.avg_views_per_day - trend.avg_views_per_day_prev) / trend.avg_views_per_day_prev) * 100).toFixed(1);
    const engDeltaPct = (((trend.avg_engagement_rate - trend.avg_engagement_rate_prev) / trend.avg_engagement_rate_prev) * 100).toFixed(1);
    grid.appendChild(statTile({ label: "평균 조회수 (하루 기준)", value: fmtInt(trend.avg_views_per_day) + "회", delta: { good: true, pct: viewsDeltaPct } }));
    grid.appendChild(statTile({ label: "평균 참여율 (좋아요+댓글 / 조회수)", value: fmtPct(trend.avg_engagement_rate), delta: { good: true, pct: engDeltaPct } }));
    grid.appendChild(statTile({ label: "평균 영상 길이", value: fmtDuration(trend.avg_duration_sec) }));

    Charts.renderHBarChart(document.getElementById("duration-dist-chart"), {
      items: trend.duration_distribution.map((d) => ({ label: d.label, value: d.pct, colorVar: "--series-3" })),
      valueFormatter: (n) => n + "%",
      labelWidth: 88
    });

    Charts.renderHBarChart(document.getElementById("subscriber-tier-chart"), {
      items: trend.subscriber_tiers.map((d) => ({ label: d.label, value: d.ratio, colorVar: "--series-1" })),
      valueFormatter: (n) => n.toFixed(1) + "배",
      labelWidth: 150
    });

    const smallTierLabel = trend.subscriber_tiers[0].label.split(" ")[0];
    document.getElementById("trend-insight-body").textContent =
      `구독자 대비 조회수가 가장 높았던 채널들을 보면, 대형 채널보다 구독자 10만 명 이하의 채널에서 더 많이 나왔어요. 업로드 시간대와 영상 길이만 잘 맞춰도 구독자 규모와 상관없이 좋은 반응을 얻을 수 있는 여지가 있다는 뜻이에요.`;

    document.getElementById("trend-footer-note").textContent =
      `이 정보는 최근 1년 이내 공개된 일반 영상 ${fmtInt(trend.sample_size)}건(${categoryInfo(currentCategory).label})을 분석한 결과예요. 데이터는 주기적으로 업데이트됩니다. ◆ 지표 일부는 예시 데이터입니다.`;
  }

  /* ---------------- Tabs ---------------- */
  const TAB_RENDERERS = {
    "tab-home": renderHome,
    "tab-channels": renderChannels,
    "tab-guide": renderGuide,
    "tab-trend": renderTrend
  };

  function renderActiveTab() {
    const activeBtn = document.querySelector(".tab-button.active");
    const fn = TAB_RENDERERS[activeBtn.dataset.target];
    if (fn) fn();
  }

  function setupTabs() {
    const buttons = document.querySelectorAll(".tab-button");
    const panels = document.querySelectorAll(".tab-panel");
    buttons.forEach((btn) => {
      btn.addEventListener("click", () => {
        buttons.forEach((b) => b.classList.remove("active"));
        panels.forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById(btn.dataset.target).classList.add("active");
        renderActiveTab();
      });
    });
  }

  async function init() {
    renderCategoryChips();
    setupTabs();
    try {
      const [meta, videoPool, channelPool, uploadHeatmap, categoryTrend] = await Promise.all([
        DataSource.fetchMeta(),
        DataSource.fetchVideoPool(),
        DataSource.fetchChannelPool(),
        DataSource.fetchUploadHeatmap(),
        DataSource.fetchCategoryTrend()
      ]);
      DATA.videoPool = videoPool;
      DATA.channelPool = channelPool;
      DATA.uploadHeatmap = uploadHeatmap;
      DATA.categoryTrend = categoryTrend;
      setMetaBadge(meta);
      renderActiveTab();
    } catch (err) {
      console.error(err);
      const banner = document.getElementById("error-banner");
      banner.textContent = "데이터를 불러오는 중 문제가 발생했습니다: " + err.message;
      banner.style.display = "block";
    }
  }

  init();
})();
