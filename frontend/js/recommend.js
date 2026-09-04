/**
 * recommend.js
 * ------------
 * "추천" 관련 계산을 전담하는 모듈입니다. 회의에서 정한 두 가지 원칙을 코드로
 * 구현합니다.
 *
 *  (1) 단순히 조회수가 높은 영상/채널만 추천하지 않는다 — 중앙값과 구독자 수
 *      대비 성과를 함께 본다. (videos.list/channels.list로 가져온 원본 후보군을
 *      받아서 스코어링하는 scoreVideoPool / scoreChannelPool가 이 부분입니다.)
 *
 *  (2) "왜 좋은지" 설명 문구는 지금은 Amazon Bedrock 같은 LLM을 붙이지 않고,
 *      조건별로 미리 써둔 규칙 기반 템플릿 문장을 고릅니다. 나중에 Bedrock으로
 *      교체할 때는 이 모듈의 explain 계열 함수 내부만 "이미 계산된 지표를
 *      프롬프트에 넣어 LLM이 문장을 생성" 하는 방식으로 바꾸면 되고, 호출부
 *      (app.js)는 그대로 둘 수 있습니다.
 */
const Recommend = (() => {
  const cfg = window.APP_CONFIG;

  function median(nums) {
    if (!nums.length) return 0;
    const s = [...nums].sort((a, b) => a - b);
    const mid = Math.floor(s.length / 2);
    return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
  }

  const fmtManwon = (n) => (n / 10000).toFixed(1) + "만";
  const videoUrl = (videoId) => `https://www.youtube.com/watch?v=${videoId}`;
  const channelUrl = (channelId) => `https://www.youtube.com/channel/${channelId}`;
  // YouTube가 공개 제공하는 썸네일 CDN URL (API 키 불필요, video_id만 있으면 조합 가능)
  const thumbnailUrl = (videoId) => `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`;

  // 게시일을 "n일 전 / n주 전 / n개월 전 / n년 전"처럼 상대적으로 표현합니다.
  function fmtAgeRelative(days) {
    if (days < 7) return `${days}일 전`;
    if (days < 30) return `${Math.round(days / 7)}주 전`;
    if (days < 365) return `${Math.round(days / 30)}개월 전`;
    return `${(days / 365).toFixed(1)}년 전`;
  }

  /* ------------------------------------------------------------------ *
   * 영상 후보군 스코어링
   * score = (avgViewsPerDay ÷ 카테고리 중앙값) * 0.6
   *       + (구독자 대비 조회수 비율 ÷ 카테고리 중앙값) * 0.4
   * 두 지표 모두 "중앙값 대비 몇 배인가"로 정규화하므로, 구독자가 많아서
   * 절대 조회수가 큰 채널이 항상 유리해지는 걸 막습니다.
   * ------------------------------------------------------------------ */
  function scoreVideoPool(pool) {
    const withMetrics = pool.map((v) => ({
      ...v,
      avgViewsPerDay: v.view_count / Math.max(v.days_since_published, 1),
      subscriberRatio: v.view_count / Math.max(v.subscriber_count, 1),
      likeRate: v.like_count / Math.max(v.view_count, 1)
    }));
    const medViews = median(withMetrics.map((v) => v.avgViewsPerDay)) || 1;
    const medSubRatio = median(withMetrics.map((v) => v.subscriberRatio)) || 1;
    const medLikeRate = median(withMetrics.map((v) => v.likeRate)) || 1;

    return withMetrics.map((v) => ({
      ...v,
      viewsVsMedian: v.avgViewsPerDay / medViews,
      subRatioVsMedian: v.subscriberRatio / medSubRatio,
      likeRateVsMedian: v.likeRate / medLikeRate,
      score: (v.avgViewsPerDay / medViews) * 0.6 + (v.subscriberRatio / medSubRatio) * 0.4,
      url: videoUrl(v.video_id),
      channelUrl: channelUrl(v.channel_id)
    }));
  }

  // "이번 주 새로 떠오른 영상"(pickNewEntries)이 게시 <=7일 구간을 전담하므로,
  // "요즘 뜨는 영상"(pickTrending)은 처음부터 그 구간을 빼고 8일~TRENDING_MAX_DAYS
  // 일 구간만 본다. 예전에는 트렌드도 <=7일까지 포함해서 최신 영상이 항상 점수를
  // 휩쓸어가는 바람에 "요즘 뜨는 영상"이 사실상 "며칠 전에 올라온 영상 목록"과
  // 다를 게 없었다 - 최근영상 편애를 걷어내고, 8일 이상 지난 영상이라도 카테고리
  // 평균 대비 성과가 좋으면 얼마든지 트렌드에 뜨도록 한다(2026-09-03 결정:
  // "최근영상 편애 완화 - 최신은 '이번 주' 섹션이 전담"). 백엔드 쪽에서도 같은 날
  // build_dashboard_data.py의 build_video_pool()을 나이 구간별 샘플링으로 바꿔서
  // 8~90일 후보 자체가 풀에 고르게 들어오도록 손봤다(views_per_day가 최근 1~2일
  // 영상을 구조적으로 편애하던 문제).
  const NEW_ENTRIES_MAX_DAYS = 7;

  function pickTrending(scoredPool, n = 3) {
    const eligible = scoredPool.filter(
      (v) => v.days_since_published > NEW_ENTRIES_MAX_DAYS && v.days_since_published <= cfg.TRENDING_MAX_DAYS
    );
    // 점수 하한선(트렌드 후보군 자체의 중앙값) - 그 카테고리에서 "평균 이상"인
    // 영상만 트렌드로 인정한다(2026-09-03 결정: "트렌드에 점수 하한선 적용").
    const floor = median(eligible.map((v) => v.score));
    return eligible
      .filter((v) => v.score >= floor)
      .sort((a, b) => b.score - a.score)
      .slice(0, n);
  }

  // "비교 기준을 절대값 대신 변화율/진입 이벤트로" 요청 대응 - pickTrending과 같은
  // 중앙값 대비 스코어링 패턴을 그대로 쓰되, 기간을 7일로 좁혀서 "이번 주에 새로
  // 떠오른" 영상만 추린다. pickTrending이 이미 NEW_ENTRIES_MAX_DAYS(7일) 이하를
  // 자기 후보군에서 빼므로(위 pickTrending 주석 참고), 두 섹션은 애초에 겹칠 수
  // 없는 날짜 구간을 나눠 갖는다 - 예전에 있던 excludeIds 파라미터(트렌드에 뽑힌
  // video_id를 여기서 다시 빼는 방식)는 이제 항상 빈 집합과 같아서 제거했다
  // (2026-09-03 결정). 정렬 기준은 점수순이 아니라 게시일 최신순으로, "가장
  // 최근에 올라온 것부터"가 되게 했다(점수는 동률일 때만 tie-break로 씀).
  function pickNewEntries(scoredPool, n = 3, maxDays = NEW_ENTRIES_MAX_DAYS) {
    return scoredPool
      .filter((v) => v.days_since_published <= maxDays)
      .sort((a, b) => a.days_since_published - b.days_since_published || b.score - a.score)
      .slice(0, n);
  }

  function pickSteady(scoredPool, n = 3) {
    return scoredPool
      .filter((v) => v.days_since_published >= cfg.STEADY_MIN_DAYS)
      .sort((a, b) => b.score - a.score)
      .slice(0, n);
  }

  // "왜 이 영상인가" 규칙 기반 설명 문구 (조건을 우선순위대로 검사해서 첫 매치를 사용)
  function explainVideo(v) {
    const isSteady = v.days_since_published >= cfg.STEADY_MIN_DAYS;

    if (!isSteady && v.viewsVsMedian >= 2.2) {
      return `게시 ${v.days_since_published}일 만에 조회수 상승 속도가 카테고리 상위권이에요`;
    }
    if (v.subRatioVsMedian >= 2.0) {
      return `구독자 대비 조회수가 평소보다 ${v.subRatioVsMedian.toFixed(1)}배 높아요`;
    }
    if (v.likeRateVsMedian >= 1.6) {
      return `좋아요 비율이 카테고리 평균의 ${v.likeRateVsMedian.toFixed(1)}배예요`;
    }
    if (isSteady && v.viewsVsMedian >= 1.3) {
      const perDay = Math.round(v.avgViewsPerDay).toLocaleString("ko-KR");
      return `게시 ${v.days_since_published}일이 지났는데도 하루 평균 ${perDay}회씩 꾸준히 유입되고 있어요`;
    }
    if (isSteady) {
      return `구독자 규모 대비 누적 조회수가 꾸준히 좋아요`;
    }
    return `카테고리 평균보다 반응이 좋은 영상이에요`;
  }

  /* ------------------------------------------------------------------ *
   * 채널 후보군 스코어링 — 영상 1건당 평균 조회수를 구독자 수로 나눈 값을
   * 카테고리 중앙값과 비교합니다. (역시 절대 구독자 수가 아니라 "규모 대비
   * 성과"로 비교)
   * ------------------------------------------------------------------ */
  function scoreChannelPool(pool) {
    const withMetrics = pool.map((c) => ({
      ...c,
      viewsPerSub: c.avg_views_per_video / Math.max(c.subscriber_count, 1)
    }));
    const medViewsPerSub = median(withMetrics.map((c) => c.viewsPerSub)) || 1;
    const medEngagement = median(withMetrics.map((c) => c.avg_engagement_rate)) || 1;
    const medSubs = median(withMetrics.map((c) => c.subscriber_count)) || 1;

    return withMetrics.map((c) => ({
      ...c,
      viewsPerSubVsMedian: c.viewsPerSub / medViewsPerSub,
      engagementVsMedian: c.avg_engagement_rate / medEngagement,
      medSubs,
      score: c.viewsPerSub / medViewsPerSub,
      url: channelUrl(c.channel_id),
      representativeUrl: videoUrl(c.representative_video.video_id)
    }));
  }

  function pickTopChannels(scoredPool, n = 3) {
    return [...scoredPool].sort((a, b) => b.score - a.score).slice(0, n);
  }

  // channel_id를 시드로 목록에서 하나를 결정적으로 고른다 - 같은 채널은 새로고침해도
  // 항상 같은 문구가 나오되(그래야 산만하지 않음), 조건이 같은 여러 채널 사이에는
  // 다양한 표현이 섞이도록 하기 위함 (gradientForKey와 같은 방식, app.js 참고).
  function pickVariant(list, seedKey) {
    const str = String(seedKey || "");
    let hash = 0;
    for (let i = 0; i < str.length; i++) hash = (hash * 31 + str.charCodeAt(i)) >>> 0;
    return list[hash % list.length];
  }

  // "구독자 규모별로 비교해봤어요" 차트(app.js renderTrend)와 같은 절대 기준
  // (build_dashboard_data.py의 tiers_def: 소형 10만 미만/중형 10만~50만/대형 50만 이상).
  const CHANNEL_TIER_SMALL_MAX = 100_000;
  const CHANNEL_TIER_LARGE_MIN = 500_000;

  // "왜 이 채널인가" 규칙 기반 하이라이트 문구.
  // 2026-09-04: 예전엔 "구독자 수 < 후보군 전체 median"만 보고 "소형 채널"이라고
  // 표시했는데, 이 median이 후보군 전체(대형 채널도 섞여있는 집합) 기준이다 보니
  // 구독자 수십만~100만대인 채널도 "소형 채널" 문구가 뜨는 문제가 있었다(사용자
  // 리포트 - 추천 채널이 거의 다 같은 문구였음). 이제는 실제 구독자 규모 구간으로
  // 판단하고, 조건별로 여러 문구 중 하나를 골라 다양성도 준다.
  function explainChannel(c) {
    const subsLabel = `${fmtManwon(c.subscriber_count)} 명`;

    if (c.avg_engagement_rate >= 0.06 && c.engagementVsMedian >= 1.3) {
      const pct = (c.avg_engagement_rate * 100).toFixed(1);
      return pickVariant([
        `참여율이 ${pct}%로 카테고리 평균보다 훨씬 높아요`,
        `좋아요·댓글 반응이 유난히 활발한 채널이에요 (참여율 ${pct}%)`
      ], c.channel_id);
    }
    if (c.upload_freq_per_week && c.upload_freq_per_week >= 3) {
      return pickVariant([
        `업로드 주기·시간대가 일정해서 참고하기 좋아요`,
        `꾸준한 업로드 페이스를 유지하고 있어서 루틴을 참고하기 좋아요`
      ], c.channel_id);
    }
    if (c.subscriber_count < CHANNEL_TIER_SMALL_MAX && c.viewsPerSubVsMedian >= 1.3) {
      return pickVariant([
        `소형 채널(구독자 ${subsLabel})인데도 구독자 대비 조회수가 평균의 ${c.viewsPerSubVsMedian.toFixed(1)}배예요`,
        `구독자 ${subsLabel} 규모의 소형 채널인데 성장세가 눈에 띄어요`
      ], c.channel_id);
    }
    if (c.subscriber_count >= CHANNEL_TIER_LARGE_MIN) {
      return pickVariant([
        `구독자 ${subsLabel}의 대형 채널답게 조회수 성과가 안정적이에요`,
        `이미 자리 잡은 대형 채널인데도 구독자 대비 조회수 반응이 꾸준해요`
      ], c.channel_id);
    }
    if (c.viewsPerSubVsMedian >= 1.5) {
      return pickVariant([
        `구독자 대비 조회수가 카테고리 평균의 ${c.viewsPerSubVsMedian.toFixed(1)}배예요`,
        `중형 채널 중에서는 구독자 대비 조회수 성과가 상위권이에요`
      ], c.channel_id);
    }
    return pickVariant([
      `구독자 대비 조회수 성과가 꾸준히 좋은 채널이에요`,
      `구독자 규모에 비해 조회수 반응이 안정적으로 좋은 채널이에요`
    ], c.channel_id);
  }

  /* ------------------------------------------------------------------ *
   * 업로드 시간대 팁 — "언제가 좋은가"는 히트맵 셀 중 최댓값 계산으로 끝.
   * "왜 좋은가"는 요일 그룹별로 미리 써둔 규칙 기반 템플릿에서 골라 채웁니다.
   * (추후 Bedrock 등으로 교체할 때는 이 함수 내부만 LLM 호출로 바꾸면 됩니다.)
   * ------------------------------------------------------------------ */
  // avg_views가 null인 셀은 그 요일×시간대에 표본이 아예 없다는 뜻(nodata)이라
  // "가장 좋은 시간대" 후보에서 제외합니다.
  function bestCell(cells) {
    const valid = cells.filter((c) => c.avg_views !== null && c.avg_views !== undefined);
    if (!valid.length) return null;
    return valid.reduce((best, c) => (c.avg_views > best.avg_views ? c : best), valid[0]);
  }

  // day: 0=월요일 ... 6=일요일 (요일별 개별 집계, 2026-09-02 결정 반영).
  // 월~금은 "평일 중에서도 ○요일"로, 토/일은 각각 전용 문구로 안내합니다.
  const DAY_TIP_TEMPLATES = {
    weekday: (dayLabel, slot) => `평일 중에서도 ${dayLabel} ${slot}에 반응이 좋아요. 상대적으로 경쟁이 적은 시간대라 신규 채널에도 추천해요.`,
    5: (dayLabel, slot) => `주말 중에서도 토요일 ${slot}에 반응이 가장 좋아요. 다만 비슷한 콘텐츠가 몰리는 시간대라 차별화가 중요해요.`,
    6: (dayLabel, slot) => `일요일 ${slot}에 반응이 좋아요. 다음 주를 준비하며 여유롭게 시청하는 사람이 많은 시간대예요.`
  };

  function uploadTip(cells) {
    const best = bestCell(cells);
    if (!best) {
      return {
        best: null,
        dayLabel: null,
        slotLabel: null,
        text: "아직 이 카테고리는 요일·시간대별로 비교할 만한 데이터가 충분히 모이지 않았어요 (nodata). 수집이 더 진행되면 채워집니다.",
        noData: true
      };
    }
    const dayLabel = cfg.DAY_GROUPS[best.day];
    const slotLabel = cfg.TIME_SLOTS[best.slot];
    const template = DAY_TIP_TEMPLATES[best.day] || DAY_TIP_TEMPLATES.weekday;
    return {
      best,
      dayLabel,
      slotLabel,
      text: template(dayLabel, slotLabel),
      noData: false
    };
  }

  return {
    median,
    fmtManwon,
    videoUrl,
    channelUrl,
    thumbnailUrl,
    fmtAgeRelative,
    scoreVideoPool,
    pickTrending,
    pickNewEntries,
    pickSteady,
    explainVideo,
    scoreChannelPool,
    pickTopChannels,
    explainChannel,
    bestCell,
    uploadTip
  };
})();
