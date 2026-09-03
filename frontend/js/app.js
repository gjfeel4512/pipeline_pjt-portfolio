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

  /* ---------------- 프로필 사진 폴백 체인 ----------------
   * 채널 프로필 사진(avatar_url, channels.list 원본 데이터)이 있으면 그걸 먼저 쓰고,
   * 로드 실패(또는 애초에 없음)하면 대표/본인 영상 썸네일로, 그것도 실패하면
   * <img>를 제거해서 옆에 같이 넣어둔 이니셜 텍스트가 드러나게 합니다.
   * onerror 인라인 핸들러는 전역 스코프에서 실행되므로 window에 붙여둡니다. */
  window.__avatarFallback = function (img) {
    const rest = img.getAttribute("data-fallback");
    if (rest) {
      const parts = rest.split("|||");
      const next = parts.shift();
      img.setAttribute("data-fallback", parts.join("|||"));
      img.src = next;
    } else {
      img.remove();
    }
  };

  function imgFallbackHtml(sources) {
    const valid = sources.filter(Boolean);
    if (!valid.length) return "";
    const rest = valid.slice(1).join("|||");
    return `<img src="${valid[0]}" data-fallback="${rest}" onerror="window.__avatarFallback(this)" alt="">`;
  }

  const AVATAR_GRADIENTS = [
    "linear-gradient(135deg, #1baf7a, #12805a)",
    "linear-gradient(135deg, #2a78d6, #1c5aa8)",
    "linear-gradient(135deg, #eb6834, #b84e22)"
  ];

  // 채널 프로필 사진이 없어서 이니셜 배지를 보여줄 때, 같은 채널이면 홈 화면 영상 카드든
  // 채널 카드든 항상 같은 색이 나오도록 채널 id(또는 이름) 문자열을 해시해서 색을 고정합니다.
  function gradientForKey(key) {
    const str = String(key || "");
    let hash = 0;
    for (let i = 0; i < str.length; i++) hash = (hash * 31 + str.charCodeAt(i)) >>> 0;
    return AVATAR_GRADIENTS[hash % AVATAR_GRADIENTS.length];
  }

  function setMetaBadge(meta) {
    const badge = document.getElementById("meta-badge");
    const isReal = meta.source === "pipeline_snapshot";
    const kindLabel = isReal
      ? "실제 수집 데이터(파이프라인 스냅샷)"
      : cfg.USE_MOCK ? "샘플(mock) 데이터" : "실시간 데이터";
    badge.textContent = `${kindLabel} · 마지막 업데이트 ${meta.last_updated.slice(0, 10)}`;

    const realNote = (about) => `◆ 이 화면의 ${about}는 실제 수집된 데이터를 집계한 결과예요. 파이프라인이 다시 돌 때마다 자동으로 최신화됩니다.`;
    const sampleNote = (about) => `◆ 이 화면의 ${about}는 예시 데이터입니다. 실제 서비스에서는 최근 수집된 데이터로 자동 갱신됩니다.`;
    const note = isReal ? realNote : sampleNote;
    document.getElementById("home-section-note").textContent = note("영상·수치");
    document.getElementById("channels-section-note").textContent = note("채널·수치");
    document.getElementById("guide-section-note").textContent = note("시간대별 조회수·영상 길이");
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
    // 트렌드/스테디 모두 게시일(상대 표현)과 구독자 수를 함께 보여줍니다.
    const subLine = `게시 ${Recommend.fmtAgeRelative(v.days_since_published)} · 구독자 ${Recommend.fmtManwon(v.subscriber_count)}`;
    const statsLine = isTrending
      ? `<span>조회수 <strong>${Recommend.fmtManwon(v.view_count)}</strong></span><span>좋아요 <strong>${Recommend.fmtManwon(v.like_count)}</strong></span>`
      : `<span>누적 조회수 <strong>${Recommend.fmtManwon(v.view_count)}</strong></span><span>좋아요 <strong>${Recommend.fmtManwon(v.like_count)}</strong></span>`;
    // 실제 유튜브 썸네일(video_id 기반 공개 CDN)을 배경으로 쓰고, 카테고리 그라디언트는
    // 이미지가 없거나 로드 실패했을 때만 보이는 두 번째 배경 레이어로 둡니다.
    const thumbStyle = `background-image:url('${Recommend.thumbnailUrl(v.video_id)}'), ${grad};`;
    const channelInitial = (v.channel_name || "?").slice(0, 1);
    const channelAvatarGrad = gradientForKey(v.channel_id || v.channel_name);

    const el = document.createElement("div");
    el.className = "vcard";
    el.innerHTML = `
      <a class="thumb-link" href="${v.url}" target="_blank" rel="noopener">
        <div class="thumb" style="${thumbStyle}">
          ${svgPlayIcon()}
          <span class="tag-badge">${tag}</span>
          <span class="dur-badge">${fmtDuration(v.duration_sec)}</span>
        </div>
      </a>
      <div class="vcard-body">
        <a class="vcard-title" href="${v.url}" target="_blank" rel="noopener">${v.title}</a>
        <div class="vcard-channel">
          <span class="vcard-channel-avatar" style="background:${channelAvatarGrad};">${imgFallbackHtml([v.channel_avatar_url])}${channelInitial}</span>
          <a href="${v.channelUrl}" target="_blank" rel="noopener">${v.channel_name}</a>
          <span>· ${subLine}</span>
        </div>
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
    const calloutBody = tip.noData
      ? tip.text
      : `${tip.dayLabel} ${tip.slotLabel}에 올린 영상들이 조회수가 가장 잘 나왔어요. ${tip.text}`;
    callout.innerHTML = `
      <div class="tip-callout-icon">💡</div>
      <div style="flex:1;">
        <div class="tip-callout-title">이번 주 ${catInfo.label} 카테고리 업로드 추천 타이밍</div>
        <div class="tip-callout-body">${calloutBody}</div>
      </div>
    `;

    const noDataNote = (label) =>
      `<div class="nodata-note">아직 이 카테고리에서 "${label}" 조건에 맞는 영상이 충분하지 않아요 (nodata). 수집이 더 진행되면 채워집니다.</div>`;

    const trendingGrid = document.getElementById("trending-grid");
    trendingGrid.innerHTML = "";
    if (trending.length) {
      trending.forEach((v) => trendingGrid.appendChild(videoCard(v, "trending")));
    } else {
      trendingGrid.innerHTML = noDataNote("요즘 뜨는 영상");
    }

    const steadyGrid = document.getElementById("steady-grid");
    steadyGrid.innerHTML = "";
    if (steady.length) {
      steady.forEach((v) => steadyGrid.appendChild(videoCard(v, "steady")));
    } else {
      steadyGrid.innerHTML = noDataNote("꾸준히 사랑받는 영상");
    }
  }

  /* ---------------- 추천 채널 ---------------- */
  function channelCard(c, idx) {
    const initial = c.name.slice(0, 1);
    // 채널 프로필 사진(avatar_url)은 channels.list 원본 응답(outputs/bronze_collect/
    // channels_detail.jsonl.gz 또는 Silver dim_channel.channel_thumbnail_url)에서 가져온
    // 실제 채널 사진입니다. 커버리지가 100%가 아니라서(일부 채널은 값이 없음) 없으면
    // 이니셜로 대체합니다. 예전엔 대표 영상 썸네일로 한 단계 더 폴백했었는데, 그러면
    // "채널 프로필 사진"이라는 자리에 그 채널의 어떤 한 영상 썸네일이 대신 나와서 다른
    // 채널 사진으로 착각하기 쉬웠습니다(vcard-channel-avatar에서 고쳤던 것과 같은 문제) -
    // 그래서 여기서도 영상 썸네일 폴백은 빼고 이니셜로 바로 넘어가게 통일했습니다.
    const avatarGrad = AVATAR_GRADIENTS[idx % AVATAR_GRADIENTS.length];
    const avatarSources = [c.avatar_url];
    const avatarInner = `${imgFallbackHtml(avatarSources)}${initial}`;
    const el = document.createElement("div");
    el.className = "chcard";
    el.innerHTML = `
      <div class="chcard-head">
        <div class="chcard-avatar" style="background:${avatarGrad};">${avatarInner}</div>
        <div>
          <div class="chcard-name"><a href="${c.url}" target="_blank" rel="noopener">${c.name}</a></div>
          <div class="chcard-subs">구독자 ${Recommend.fmtManwon(c.subscriber_count)}</div>
        </div>
      </div>
      <div class="chcard-highlight">📌 ${Recommend.explainChannel(c)}</div>
      <div class="chcard-desc">
        영상 1건당 평균 조회수 ${Recommend.fmtManwon(c.avg_views_per_video)} · 참여율 ${fmtPct(c.avg_engagement_rate)}
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
      const dayCells = cells.filter((c) => c.day === di && c.avg_duration_sec !== null && c.avg_duration_sec !== undefined);
      const span = document.createElement("span");
      if (dayCells.length) {
        const avg = dayCells.reduce((s, c) => s + c.avg_duration_sec, 0) / dayCells.length;
        span.innerHTML = `${dLabel} 평균 영상 길이 <strong>${fmtDuration(avg)}</strong>`;
      } else {
        span.innerHTML = `${dLabel} 평균 영상 길이 <strong>nodata</strong>`;
      }
      durationRow.appendChild(span);
    });

    const tip = Recommend.uploadTip(cells);

    const checklist = document.getElementById("checklist");
    checklist.innerHTML = "";
    const items = [
      tip.noData ? `업로드 시간대 데이터가 더 쌓이면 추천 시간대가 여기 표시돼요 (nodata)` : `${tip.dayLabel} ${tip.slotLabel}에 업로드 예약해보기`,
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
  function statTile({ label, value, delta, noPrevData }) {
    const tile = document.createElement("div");
    tile.className = "stat-tile";
    let deltaHtml = "";
    if (delta) {
      deltaHtml = `<div class="stat-delta ${delta.good ? "good" : ""}">▲ 지난 기간 대비 ${delta.pct}%</div>`;
    } else if (noPrevData) {
      deltaHtml = `<div class="stat-delta nodata">지난 기간 대비 nodata (첫 집계)</div>`;
    }
    tile.innerHTML = `<div class="stat-label">${label}</div><div class="stat-value">${value}</div>${deltaHtml}`;
    return tile;
  }

  function renderTrend() {
    const trend = DATA.categoryTrend[currentCategory];

    const grid = document.getElementById("trend-stats");
    grid.innerHTML = "";
    // prev 값은 "지난 기간 대비" 비교용인데, 아직 실데이터로는 이전 주 스냅샷이 없어서
    // (파이프라인이 이번이 첫 집계) null(nodata)입니다 — 이 경우 증감 표시를 생략합니다.
    const hasViewsPrev = trend.avg_views_per_day_prev !== null && trend.avg_views_per_day_prev !== undefined;
    const hasEngPrev = trend.avg_engagement_rate_prev !== null && trend.avg_engagement_rate_prev !== undefined;
    const viewsDeltaPct = hasViewsPrev
      ? (((trend.avg_views_per_day - trend.avg_views_per_day_prev) / trend.avg_views_per_day_prev) * 100).toFixed(1)
      : null;
    const engDeltaPct = hasEngPrev
      ? (((trend.avg_engagement_rate - trend.avg_engagement_rate_prev) / trend.avg_engagement_rate_prev) * 100).toFixed(1)
      : null;
    grid.appendChild(statTile({ label: "평균 조회수 (하루 기준)", value: fmtInt(trend.avg_views_per_day) + "회", delta: hasViewsPrev ? { good: true, pct: viewsDeltaPct } : null, noPrevData: !hasViewsPrev }));
    grid.appendChild(statTile({ label: "평균 참여율 (좋아요+댓글 / 조회수)", value: fmtPct(trend.avg_engagement_rate), delta: hasEngPrev ? { good: true, pct: engDeltaPct } : null, noPrevData: !hasEngPrev }));
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

    renderMetadataImpact();
    renderTopicTrends();

    const smallTierLabel = trend.subscriber_tiers[0].label.split(" ")[0];
    document.getElementById("trend-insight-body").textContent =
      `구독자 대비 조회수가 가장 높았던 채널들을 보면, 대형 채널보다 구독자 10만 명 이하의 채널에서 더 많이 나왔어요. 업로드 시간대와 영상 길이만 잘 맞춰도 구독자 규모와 상관없이 좋은 반응을 얻을 수 있는 여지가 있다는 뜻이에요.`;

    document.getElementById("trend-footer-note").textContent =
      `이 정보는 실제 수집된 영상 ${fmtInt(trend.sample_size)}건(${categoryInfo(currentCategory).label})을 분석한 결과예요. 데이터는 파이프라인이 갱신될 때마다 최신화됩니다. ◆ "지난 기간 대비" 수치는 다음 주 재집계부터 표시돼요(이번이 첫 집계라 nodata).`;
  }

  // spec.md 분석 7(메타데이터 최적화): frontend/scripts/build_metadata_impact.py가
  // outputs/silver/*.jsonl로 카테고리별 다중회귀(OLS)를 돌려 만든 결과를 막대로 보여줌.
  // 표본이 30건 미만인 카테고리는 스크립트가 null로 내려주므로(지어내지 않음) nodata 처리.
  // 통계적으로 유의하지 않은(p>=0.05) 요인은 근거가 약하다는 뜻이라 막대 색을 흐리게(--text-muted) 표시.
  function renderMetadataImpact() {
    const result = DATA.metadataImpact ? DATA.metadataImpact[currentCategory] : null;
    const chartRoot = document.getElementById("metadata-impact-chart");
    const footnote = document.getElementById("metadata-impact-footnote");

    if (!result) {
      chartRoot.innerHTML = '<p class="card-footnote">아직 이 카테고리는 표본이 부족해서 분석할 수 없어요 (nodata).</p>';
      footnote.textContent = "";
      return;
    }

    const items = result.features.map((f) => ({
      label: f.label + (f.significant ? "" : " (근거 부족)"),
      value: Math.abs(f.effect_pct),
      colorVar: f.significant ? (f.effect_pct >= 0 ? "--series-3" : "--series-2") : "--text-muted",
      suffix: "%",
      _signed: f.effect_pct
    }));

    Charts.renderHBarChart(chartRoot, {
      items,
      valueFormatter: () => "",
      labelWidth: 150
    });
    // renderHBarChart의 valueFormatter는 부호 없는 절대값(막대 길이용)만 받으므로,
    // 실제 라벨(부호 포함 %)은 렌더링 이후 직접 덮어씀.
    const valueLabels = chartRoot.querySelectorAll(".viz-value-label");
    valueLabels.forEach((el, i) => {
      const v = items[i]._signed;
      el.textContent = (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
    });

    footnote.textContent =
      `실제 영상 ${result.sample_size.toLocaleString("ko-KR")}건을 분석한 추정치예요(설명력 R²=${result.r_squared}). ` +
      `"근거 부족"이라고 표시된 항목은 통계적으로 확실하지 않다는 뜻이니 참고만 해주세요. ` +
      `이건 상관관계이지 "이렇게 하면 반드시 이렇게 된다"는 인과관계가 아니에요.`;
  }

  // 태그 기반 주제 군집 + 트렌드(사용자 요청): frontend/scripts/build_topic_trends.py가
  // outputs/silver/*.jsonl의 태그를 TF-IDF+KMeans로 군집화해 "주제"를 자동 추출하고,
  // 게시일 중앙값으로 나눈 예전/최근 절반의 나이보정 상대성과 변화율(trend_pct)을 계산.
  // 실측 데이터 100% 사용(합성 아님) - 원래 나이보정 없이 계산했다가 모든 군집이
  // +200~1400%로 나와서 비현실적이라 검증해보니 최근 영상일수록 아직 초기 조회
  // 몰림 구간이라 값이 부풀어 보이는 편향이었음(스크립트 _comment 참고) - 나이대
  // 또래 대비 상대값으로 정규화해서 고침.
  function renderTopicTrends() {
    const result = DATA.topicTrends ? DATA.topicTrends[currentCategory] : null;
    const chartRoot = document.getElementById("topic-trends-chart");
    const footnote = document.getElementById("topic-trends-footnote");

    if (!result) {
      chartRoot.innerHTML = '<p class="card-footnote">아직 이 카테고리는 표본이 부족해서 분석할 수 없어요 (nodata).</p>';
      footnote.textContent = "";
      return;
    }

    const items = result.clusters.map((c) => {
      const hasTrend = c.trend_pct !== null && c.trend_pct !== undefined;
      const trendText = hasTrend
        ? ` · ${c.trend_pct >= 0 ? "+" : ""}${c.trend_pct.toFixed(1)}%${c.trend_pct >= 0 ? " ▲" : " ▼"}`
        : " · 표본부족(nodata)";
      return {
        label: c.top_terms.slice(0, 3).join(" · ") || `주제 ${c.cluster_id}`,
        value: c.median_views_per_day,
        colorVar: !hasTrend ? "--text-muted" : c.trend_pct >= 0 ? "--series-3" : "--series-2",
        suffix: trendText
      };
    });

    Charts.renderHBarChart(chartRoot, {
      items,
      labelWidth: 170
    });

    footnote.textContent =
      `실제 영상 ${result.sample_size.toLocaleString("ko-KR")}건의 태그를 자동으로 묶은 결과예요(주제 이름은 사람이 붙인 게 ` +
      `아니라 태그 군집에서 뽑은 키워드예요). 막대 길이는 일평균 조회수, %는 ${result.median_split_date} 기준 예전/최근 절반을 ` +
      `같은 나이대 또래 대비 상대값으로 비교한 변화율이에요. 표본이 작은 주제는 우연한 변동일 수 있으니 참고만 해주세요.`;
  }

  /* ---------------- 심화분석(데모) ---------------- */
  // spec.md 분석 5(성장곡선 유형화) / 분석 6(판단 시점 회귀): 실제로는 한 영상을
  // 여러 시점에 걸쳐 추적해야(적응형 시계열) 가능한데, 2026-09-03 기준 실측 영상의
  // 96%가 스냅샷 1개뿐이라 아직 불가능해서 합성(synthetic) 데이터로 기법만 시연.
  // 카테고리 필터와 무관하게(currentCategory 미사용) 항상 같은 내용을 보여줌.
  function renderDemo() {
    const demo = DATA.syntheticDemo;
    if (!demo) return;

    const clusterInfo = demo.growth_curve_clusters;
    const clusterColors = ["--series-1", "--series-2", "--series-3"];
    Charts.renderLineChart(document.getElementById("growth-cluster-chart"), {
      days: clusterInfo.days,
      series: clusterInfo.clusters.map((c, i) => ({
        label: `${c.label} (${c.sample_count}건)`,
        colorVar: clusterColors[i % clusterColors.length],
        points: c.curve
      })),
      xLabel: "",
      yLabel: "조회수"
    });
    document.getElementById("growth-cluster-footnote").textContent =
      `[합성 데이터] ${clusterInfo.note} 실제 영상이 아니라 3가지 곡선 유형을 본떠 만든 예시 데이터예요.`;

    const timing = demo.judgment_timing;
    Charts.renderHBarChart(document.getElementById("judgment-timing-chart"), {
      items: timing.results.map((r) => ({
        label: r.label,
        value: r.r_squared,
        colorVar: "--series-1",
        suffix: ""
      })),
      valueFormatter: (n) => n.toFixed(2),
      labelWidth: 110
    });
    document.getElementById("judgment-timing-footnote").textContent =
      `[합성 데이터] ${timing.note} 실제 영상이 아니라 카테고리 특성을 본떠 만든 예시 데이터예요.`;

    const growth = demo.channel_growth;
    const growthColors = ["--series-1", "--series-2", "--series-3"];
    Charts.renderLineChart(document.getElementById("channel-growth-chart"), {
      days: growth.days,
      series: growth.channels.map((c, i) => ({
        label: `${c.label} (${fmtInt(c.start_subscribers)}→${fmtInt(c.end_subscribers)}명)`,
        colorVar: growthColors[i % growthColors.length],
        points: c.curve
      })),
      xLabel: "",
      yLabel: "구독자"
    });
    document.getElementById("channel-growth-footnote").textContent =
      `[합성 데이터] ${growth.note} 실제 채널이 아니라 3가지 성장 유형을 본떠 만든 예시 데이터예요.`;

    const weekly = demo.weekly_trend;
    const weeklyColors = ["--series-3", "--series-2", "--series-1"];
    Charts.renderLineChart(document.getElementById("weekly-trend-chart"), {
      days: weekly.weeks,
      series: weekly.categories.map((c, i) => ({
        label: c.label,
        colorVar: weeklyColors[i % weeklyColors.length],
        points: c.curve
      })),
      xLabel: "",
      yLabel: "평균 조회수"
    });
    Charts.renderHBarChart(document.getElementById("weekly-trend-badges"), {
      items: weekly.categories.map((c) => ({
        label: c.label,
        value: Math.abs(c.wow_change_pct),
        colorVar: c.wow_change_pct >= 0 ? "--series-3" : "--series-2",
        suffix: "%",
        _signed: c.wow_change_pct
      })),
      valueFormatter: () => "",
      labelWidth: 130
    });
    document.querySelectorAll("#weekly-trend-badges .viz-value-label").forEach((el, i) => {
      const v = weekly.categories[i].wow_change_pct;
      el.textContent = "지난 주 대비 " + (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
    });
    document.getElementById("weekly-trend-footnote").textContent =
      `[합성 데이터] ${weekly.note} 실제 주간 집계가 아니라 10주치 추이를 본떠 만든 예시 데이터예요.`;
  }

  /* ---------------- Tabs ---------------- */
  const TAB_RENDERERS = {
    "tab-home": renderHome,
    "tab-channels": renderChannels,
    "tab-guide": renderGuide,
    "tab-trend": renderTrend,
    "tab-demo": renderDemo
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
      const [meta, videoPool, channelPool, uploadHeatmap, categoryTrend, metadataImpact, syntheticDemo, topicTrends] = await Promise.all([
        DataSource.fetchMeta(),
        DataSource.fetchVideoPool(),
        DataSource.fetchChannelPool(),
        DataSource.fetchUploadHeatmap(),
        DataSource.fetchCategoryTrend(),
        DataSource.fetchMetadataImpact(),
        DataSource.fetchSyntheticDemo(),
        DataSource.fetchTopicTrends()
      ]);
      DATA.videoPool = videoPool;
      DATA.channelPool = channelPool;
      DATA.uploadHeatmap = uploadHeatmap;
      DATA.categoryTrend = categoryTrend;
      DATA.metadataImpact = metadataImpact;
      DATA.syntheticDemo = syntheticDemo;
      DATA.topicTrends = topicTrends;
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
