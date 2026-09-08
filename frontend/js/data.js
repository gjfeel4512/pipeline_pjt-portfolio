/**
 * data.js
 * -------
 * 데이터 로딩을 한 곳으로 모아두는 얇은 레이어입니다.
 * config.js의 MOCK_FILES 매핑에 있는 정적 JSON 파일을 그대로 fetch합니다.
 * 화면(app.js) 쪽에서는 어디서 데이터가 왔는지 신경 쓰지 않고
 * fetchVideoPool() 처럼 이름이 있는 함수만 호출하면 됩니다.
 *
 * 캐시를 두지 않고 호출할 때마다 매번 새로 fetch합니다 — 파이프라인이 매일
 * mock/*.json을 갱신하면 페이지를 새로고침하는 것만으로 최신 데이터가
 * 반영되게 하기 위해서입니다.
 */
const DataSource = (() => {
  const cfg = window.APP_CONFIG;

  async function load(key) {
    const url = cfg.MOCK_FILES[key];

    const res = await fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" });
    if (!res.ok) {
      throw new Error(`데이터 로드 실패 (${key}): HTTP ${res.status}`);
    }
    return res.json();
  }

  return {
    // 헤더의 "마지막 업데이트" 표시용
    fetchMeta: () => load("meta"),
    // 홈 / 카테고리 트렌드 공통 재료: 카테고리별 영상 후보군 (recommend.js가 스코어링)
    fetchVideoPool: () => load("videoPool"),
    // 추천 채널 재료: 카테고리별 채널 후보군 (recommend.js가 스코어링)
    fetchChannelPool: () => load("channelPool"),
    // 업로드 가이드: 요일×시간대 히트맵 + 평균 영상 길이
    fetchUploadHeatmap: () => load("uploadHeatmap"),
    // 카테고리 트렌드: 통계/영상 길이 분포/구독자 규모 비교
    fetchCategoryTrend: () => load("categoryTrend"),
    // 카테고리 트렌드 보조: 제목 길이·태그 수·자막 유무·영상 길이가 조회수에 미치는 영향(회귀분석)
    fetchMetadataImpact: () => load("metadataImpact"),
    // 심화분석(데모) 탭: 성장곡선 클러스터링 + 판단 시점 회귀 (합성 데이터, 기법 시연용)
    fetchSyntheticDemo: () => load("syntheticDemo"),
    // 카테고리 트렌드 보조: 태그 기반 주제 군집 + 트렌드 (실측 데이터, TF-IDF+KMeans)
    fetchTopicTrends: () => load("topicTrends"),
    // 카테고리 트렌드 보조: 1년치 실측 백필을 월별로 재생 (실측 데이터, 합성 아님)
    fetchHistoryReplay: () => load("historyReplay"),
    // 추천 채널 보조: 다른 카테고리 리뷰어 후보 (검수 필요, 실측 데이터)
    fetchCrossCategoryReviewers: () => load("crossCategoryReviewers")
  };
})();
