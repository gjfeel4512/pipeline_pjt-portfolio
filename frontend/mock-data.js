/**
 * mock-data.js — 하드코딩 목업 데이터
 * -----------------------------------------------------------------------
 * v2: 원본 기획(프론트엔드.pdf, 이창용)에서 뺐던 요소를 코드 재확인 후 복구했습니다.
 *
 * [중요 정정] 원래는 "업로드 가이드 히트맵의 요일 축을 뺀 건 백엔드가 구독자 구간
 * 축으로만 설계돼서 어쩔 수 없었다"고 설명했는데, 이건 틀린 설명이었습니다.
 * sql/youtube_pipeline_schema_postgresql.sql 을 다시 보니 gold_upload_strategy에
 * published_day_of_week 컬럼이 실제로 PK에 포함되어 있고(211~234행),
 * vw_video_analysis도 published_day_of_week_ko(월~일)를 이미 계산해서 갖고 있습니다.
 * 즉 요일 축을 뺄 타당한 이유가 없어서 복구했습니다.
 *
 * 반대로 아래 항목은 여전히 "지금 당장은 실제로 없는" 값이라 화면에는 넣되
 * 각 항목 하단 note에 실제 구현 상태를 정직하게 남겨뒀습니다:
 *  - 참여율(좋아요+댓글/조회수): comment_count는 실제로 수집되고 있고
 *    (dags/daily_lambda_to_silver_dag.py에서 engagement_rate까지 계산함) 다만
 *    이 값이 gold_category_benchmark로 아직 집계되지 않았습니다 (median_like_rate만 있음).
 *  - 기간대비 증감률(▲%): gold_category_benchmark의 PK가 (analysis_week, category_id)라
 *    주차별로 행이 쌓이는 구조라 전주 대비 비교 자체는 가능한데, compute_gold.sql에는
 *    아직 전주 비교 쿼리가 없습니다.
 * 두 항목 모두 "불가능"이 아니라 "집계 쿼리 한 단계가 더 필요한 상태"라 화면에는
 * 넣고 뱃지로 상태를 표시했습니다.
 */

window.MOCK = {
  meta: {
    serviceName: "튜브가이드",
    tagline: "크리에이터를 위한 유튜브 트렌드 인사이트",
    lastBatchAt: "2026-08-31T16:30:00+09:00",
    batchFrequency: "1일 3회 배치 수집 (Lambda, 08:30 / 16:30 / 24:30 KST)",
    totalVideos: 10163,
    totalCategories: 3,
    excludedCategoryNote:
      "인물·블로그 카테고리는 태그 데이터 오염이 확인되어 분석에서 제외했습니다.",
    dataWindow: "최근 1년 이내 공개된 일반 영상 기준",
  },

  // 필터 칩 (전체 포함, 원본처럼 모든 탭에 노출)
  filterCategories: [
    { id: "all", label: "전체" },
    { id: "game", label: "게임" },
    { id: "autos", label: "자동차·차량" },
    { id: "film", label: "영화·애니메이션" },
  ],

  categories: [
    { id: "game", label: "게임", color: "#6C5CE7" },
    { id: "autos", label: "자동차·차량", color: "#00B894" },
    { id: "film", label: "영화·애니메이션", color: "#0984E3" },
  ],

  // gold_category_benchmark 대응 + WoW 델타(집계 쿼리 추가 필요 - 상태 뱃지 있음)
  categoryBenchmark: [
    {
      categoryId: "game", categoryLabel: "게임",
      videoCount: 4128, avgViews: 182_400, avgLikes: 6_240,
      avgViewsPerDay: 3_120, medianDurationSec: 612, topUploadHour: 20,
      engagementRatePct: 4.8, engagementWowPct: 5.2, viewsWowPct: 8.9,
      durationDistribution: [
        { bucket: "1-5분", pct: 26 }, { bucket: "5-10분", pct: 41 },
        { bucket: "10-20분", pct: 24 }, { bucket: "20분 이상", pct: 9 },
      ],
      subscriberComparison: [
        { segment: "소형(1만-10만)", multiplier: 9.1 },
        { segment: "중형(10만-50만)", multiplier: 4.6 },
        { segment: "대형(50만 이상)", multiplier: 2.0 },
      ],
    },
    {
      categoryId: "autos", categoryLabel: "자동차·차량",
      videoCount: 2871, avgViews: 96_800, avgLikes: 2_180,
      avgViewsPerDay: 1_450, medianDurationSec: 780, topUploadHour: 18,
      engagementRatePct: 3.1, engagementWowPct: -1.4, viewsWowPct: 2.3,
      durationDistribution: [
        { bucket: "1-5분", pct: 12 }, { bucket: "5-10분", pct: 33 },
        { bucket: "10-20분", pct: 39 }, { bucket: "20분 이상", pct: 16 },
      ],
      subscriberComparison: [
        { segment: "소형(1만-10만)", multiplier: 7.4 },
        { segment: "중형(10만-50만)", multiplier: 3.5 },
        { segment: "대형(50만 이상)", multiplier: 1.5 },
      ],
    },
    {
      categoryId: "film", categoryLabel: "영화·애니메이션",
      videoCount: 3164, avgViews: 214_900, avgLikes: 8_910,
      avgViewsPerDay: 3_760, medianDurationSec: 495, topUploadHour: 21,
      engagementRatePct: 4.4, engagementWowPct: 6.5, viewsWowPct: 7.8,
      durationDistribution: [
        { bucket: "1-5분", pct: 22 }, { bucket: "5-10분", pct: 38 },
        { bucket: "10-20분", pct: 27 }, { bucket: "20분 이상", pct: 13 },
      ],
      subscriberComparison: [
        { segment: "소형(1만-10만)", multiplier: 8.2 },
        { segment: "중형(10만-50만)", multiplier: 4.1 },
        { segment: "대형(50만 이상)", multiplier: 1.8 },
      ],
    },
  ],

  // gold_video_rank_trend 대응 - trending_rank 기반 순위 변동 (오늘 새로 만든 백엔드 지표)
  rankTrend: [
    { videoId: "vT001", title: "신작 오픈월드 게임 첫인상 리뷰", channelName: "픽셀런처", categoryId: "game", categoryLabel: "게임", subscribers: "21.0만", firstRank: 18, latestRank: 3, rankChange: 15, viewGrowthPct: 312, views: 612_000, likes: 41_000, comments: 3_200, postedDaysAgo: 22, trendDirection: "up" },
    { videoId: "vT002", title: "전기차 장거리 주행 실측 비교", channelName: "오토그래프", categoryId: "autos", categoryLabel: "자동차·차량", subscribers: "9.5만", firstRank: 22, latestRank: 9, rankChange: 13, viewGrowthPct: 188, views: 205_000, likes: 16_000, comments: 1_800, postedDaysAgo: 15, trendDirection: "up" },
    { videoId: "vT003", title: "애니메이션 극장판 결말 해석", channelName: "씨네브릿지", categoryId: "film", categoryLabel: "영화·애니메이션", subscribers: "34.0만", firstRank: 30, latestRank: 11, rankChange: 19, viewGrowthPct: 245, views: 481_000, likes: 39_000, comments: 3_900, postedDaysAgo: 9, trendDirection: "up" },
    { videoId: "vT004", title: "인기 FPS 대회 하이라이트", channelName: "랭크업연구소", categoryId: "game", categoryLabel: "게임", subscribers: "9.5만", firstRank: 5, latestRank: 2, rankChange: 3, viewGrowthPct: 64, views: 224_000, likes: 15_600, comments: 1_100, postedDaysAgo: 4, trendDirection: "up" },
    { videoId: "vT005", title: "국산 SUV 신모델 시승기", channelName: "카리뷰센터", categoryId: "autos", categoryLabel: "자동차·차량", subscribers: "5.2만", firstRank: 14, latestRank: 21, rankChange: -7, viewGrowthPct: -18, views: 98_000, likes: 4_100, comments: 320, postedDaysAgo: 30, trendDirection: "down" },
  ],

  // "꾸준히 사랑받는 영상" - 업로드 6개월 이상, 계속 유입되는 영상 (원본에서 뺐던 섹션, 복구)
  steadySellers: [
    { videoId: "vS001", title: "역대 최고 명장면 총정리", channelName: "클리어실황", categoryId: "game", postedDaysAgo: 314, cumulativeViews: 3_120_000, likes: 187_000, insightTag: "1년 가까이 하루 평균 9,036회씩 꾸준히 유입되고 있어요" },
    { videoId: "vS002", title: "초보자를 위한 완벽 가이드", channelName: "픽셀런처", categoryId: "game", postedDaysAgo: 260, cumulativeViews: 1_860_000, likes: 94_100, insightTag: "검색을 통한 신규 시청자 유입이 가장 많은 영상이에요" },
    { videoId: "vS003", title: "전기차 배터리 완전 정복", channelName: "오토그래프", categoryId: "autos", postedDaysAgo: 201, cumulativeViews: 1_120_000, likes: 61_400, insightTag: "구독 채널 대비 조회수가 압도적으로 높아요" },
    { videoId: "vS004", title: "애니메이션 명대사 모음", channelName: "씨네브릿지", categoryId: "film", postedDaysAgo: 288, cumulativeViews: 2_240_000, likes: 156_000, insightTag: "재생목록에 가장 많이 저장된 영상이에요" },
  ],

  // 이번 주 추천 타이밍 배너 (원본에서 뺐던 요소, 복구)
  weeklyBanner: {
    game: "토요일 밤 10~11시 사이에 올린 영상들이 조회수 상승 속도가 눈에 띄게 빨랐어요. 영상 길이는 5~10분이 반응이 좋았습니다.",
    autos: "평일 오전 6~10시 사이 출근길 시간대에 올린 영상 반응이 좋아요. 시승기·정보성 콘텐츠가 특히 잘 됩니다.",
    film: "심야(22~01시) 시간대 업로드 반응이 꾸준히 좋고, 숏폼(3분 이하) 해석·요약 콘텐츠 조회수가 높아요.",
  },

  // 추천 채널 (백엔드 채널 선별 로직 미구현 - 화면 검토용 샘플, 상세정보/팁 복구)
  recommendedChannels: {
    game: [
      { name: "클리어실황", subscribers: "21.0만", reasonTag: "업로드 주기·시간대가 일정해서 참고하기 좋아요", description: "주 3회 이상, 심야(22~23시) 시간대에 집중적으로 업로드해요. 평균 좋아요율이 카테고리 평균보다 높아요.", topVideo: "역대 최고 명장면 총정리", topVideoViews: 312 },
      { name: "게임메이트", subscribers: "34.0만", reasonTag: "업데이트 타이밍을 빠르게 캐치해요", description: "신작·업데이트 발표 24시간 안에 영상을 올려요. 평균 조회수/댓글이 12,400회로 카테고리 상위권이에요.", topVideo: "업데이트 패치노트 완전 분석", topVideoViews: 20.5 },
      { name: "랭크업연구소", subscribers: "9.5만", reasonTag: "소형 채널의 성장 사례로 참고하기 좋아요", description: "구독자는 상대적으로 적지만 참여율이 8%대로 매우 높아요. 하이라이트 편집 스타일이 강점이에요.", topVideo: "역대급 랭크 하이라이트 모음", topVideoViews: 22.4 },
    ],
    autos: [
      { name: "오토그래프", subscribers: "9.5만", reasonTag: "출근길 시간대 업로드가 인상적이에요", description: "평일 오전 6~10시 사이 정기 업로드로 조회수가 안정적이에요. 실측 비교 콘텐츠가 강점이에요.", topVideo: "전기차 장거리 주행 실측 비교", topVideoViews: 20.5 },
      { name: "카리뷰센터", subscribers: "5.2만", reasonTag: "시승기 포맷을 일관되게 유지해요", description: "동일한 포맷(오프닝-주행-정리)을 유지해서 시청자 이탈이 적어요. 댓글 응답률도 높은 편이에요.", topVideo: "국산 SUV 신모델 시승기", topVideoViews: 9.8 },
      { name: "모터로그", subscribers: "18.3만", reasonTag: "정보성 콘텐츠 비중이 높아요", description: "리뷰보다 정비·유지비 정보 콘텐츠 비중이 커서 검색 유입이 꾸준해요.", topVideo: "전기차 배터리 완전 정복", topVideoViews: 11.2 },
    ],
    film: [
      { name: "씨네브릿지", subscribers: "34.0만", reasonTag: "해석 콘텐츠 반응이 꾸준히 좋아요", description: "개봉 직후 해석·결말 정리 콘텐츠를 빠르게 올려요. 재생목록 저장 비율이 카테고리 평균의 2배예요.", topVideo: "애니메이션 극장판 결말 해석", topVideoViews: 48.1 },
      { name: "필름로그", subscribers: "12.7만", reasonTag: "숏폼 요약이 특히 강해요", description: "3분 이하 숏폼 요약 콘텐츠 위주로, 심야 업로드 반응이 특히 좋아요.", topVideo: "애니메이션 명대사 모음", topVideoViews: 22.4 },
      { name: "무비체크", subscribers: "6.9만", reasonTag: "소형 채널이지만 참여율이 높아요", description: "구독자는 적지만 댓글 응답을 꾸준히 해서 커뮤니티 반응이 활발해요.", topVideo: "숨겨진 명작 애니메이션 추천", topVideoViews: 8.3 },
    ],
  },
  channelTip:
    "추천 채널을 그대로 따라 하기보다, 업로드 주기·시간대·영상 길이 패턴을 참고하는 걸 추천드려요. 콘텐츠 소재나 편집 스타일은 본인 채널의 색깔을 유지하는 게 장기적으로 더 좋은 성과로 이어지는 경우가 많았어요.",

  // 업로드 가이드: 요일(published_day_of_week) x 시간대(upload_time_bucket) 히트맵 - 복구
  // baseHeatmap 값을 구독자 구간 필터에 따라 계수로 보정해서 보여줍니다 (app.js에서 처리)
  uploadTimeBuckets: ["새벽(01-06시)", "오전(06-10시)", "오후(10-18시)", "저녁(18-22시)", "심야(22-01시)"],
  uploadDayBuckets: ["평일", "토요일", "일요일"],
  uploadHeatmap: {
    game: {
      base: [
        [320, 610, 980, 1980, 2210],
        [780, 1170, 1470, 2250, 2640],
        [750, 1130, 1410, 2170, 2540],
      ],
      sample: [
        [61, 88, 140, 210, 190],
        [42, 55, 74, 96, 88],
        [38, 50, 68, 90, 81],
      ],
      bestCell: [1, 4], // 토요일 · 심야
    },
    autos: {
      base: [
        [180, 690, 520, 610, 260],
        [220, 610, 480, 560, 240],
        [200, 560, 440, 520, 230],
      ],
      sample: [
        [40, 118, 92, 84, 33],
        [22, 61, 48, 44, 19],
        [20, 55, 43, 40, 17],
      ],
      bestCell: [0, 1], // 평일 · 오전
    },
    film: {
      base: [
        [640, 520, 610, 1240, 1980],
        [720, 590, 680, 1410, 2260],
        [700, 570, 660, 1380, 2190],
      ],
      sample: [
        [90, 70, 82, 174, 205],
        [46, 38, 44, 88, 104],
        [43, 35, 41, 82, 97],
      ],
      bestCell: [1, 4], // 토요일 · 심야
    },
  },
  subscriberSegments: [
    { id: "all", label: "전체", factor: 1 },
    { id: "under100k", label: "10만 미만", factor: 0.6 },
    { id: "100k-1m", label: "10만-100만", factor: 3.4 },
    { id: "over1m", label: "100만 이상", factor: 9.8 },
  ],

  weeklyChecklist: {
    game: ["토요일 밤 10~11시 사이에 업로드 예약해보기", "영상 길이는 5~10분 사이로 맞춰보기", "썸네일에 핵심 장면이나 텍스트를 눈에 띄게 넣기", "업로드 직후 커뮤니티 탭에 소식 남기기"],
    autos: ["평일 오전 6~10시 사이에 업로드 예약해보기", "영상 길이는 10~20분 사이로 맞춰보기", "썸네일에 차량 실물 사진을 크게 넣기", "업로드 직후 커뮤니티 탭에 소식 남기기"],
    film: ["심야(22~01시) 시간대에 업로드 예약해보기", "영상 길이는 3분 이하 숏폼도 함께 시도해보기", "썸네일에 핵심 장면을 눈에 띄게 넣기", "업로드 직후 커뮤니티 탭에 소식 남기기"],
  },
  uploadTip:
    "요일·시간대별 평균값을 그대로 따라 하기보다, 최근 내 채널 데이터와 비교해보는 걸 추천드려요. 신규 채널이라면 업로드 직후부터 꾸준히 시도해보는 것도 방법이에요.",
};
