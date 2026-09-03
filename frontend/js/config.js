/**
 * config.js
 * ----------
 * 대시보드가 데이터를 어디서 가져올지 결정하는 단일 설정 지점입니다.
 *
 * === 이 버전(goldline)의 방향 ===
 * 기존 frontend/ 는 관리자용 리포트(카테고리 벤치마크 / 업로드 히트맵 / 채널 진단
 * 리포트) 형태였습니다. 회의에서 "사용자(유튜버)가 서비스처럼 받아보는 화면"으로
 * 방향을 바꾸기로 확정해서, 이 goldline 버전은 아래 4개 화면으로 재구성했습니다.
 *   1) 홈           — 요즘 뜨는 영상(트렌드) / 꾸준히 사랑받는 영상(스테디) 추천
 *   2) 추천 채널     — 참고할 만한 채널 추천 + 유튜브 바로가기
 *   3) 업로드 가이드 — 요일×시간대 히트맵 + 평균 영상 길이 + 이번 주 체크리스트
 *   4) 카테고리 트렌드 — 카테고리 통계, 영상 길이 분포, 구독자 규모별 비교
 *
 * 카테고리는 회의 결정에 따라 게임 / 자동차·차량 / 영화·애니메이션 3종만 쓰고
 * (인물·블로그 제외), 쇼츠/숏폼은 수집 단계에서 이미 제외되므로 화면에서도 다루지
 * 않습니다. 3개 카테고리 모두 트렌드·스테디 추천을 동일하게 보여줍니다.
 *
 * 지금은 백엔드 API가 아직 없어서 USE_MOCK = true 로 두고 mock/*.json 정적 파일을
 * 읽어옵니다. 백엔드가 API를 완성하면 아래 3줄만 바꾸면 됩니다:
 *   USE_MOCK → false, API_BASE_URL, ENDPOINTS
 * data.js / charts.js / recommend.js / app.js는 손댈 필요 없습니다(응답 필드명이
 * mock/*.json과 다르면 data.js의 각 fetchXxx 함수에서 매핑만 추가해주세요).
 *
 * === 매일 갱신되는 화면으로 유지하는 방법 ===
 * 이 프론트는 정적 파일이라 "자동으로 매일 값이 바뀌는" 백엔드는 아닙니다. 대신:
 *   - 파이프라인(Airflow 등)이 하루 1회 mock/*.json (또는 실 API가 붙은 뒤에는
 *     DB/API 응답)을 그날 수집 결과로 덮어씁니다.
 *   - 프론트는 캐시 없이 페이지를 열 때마다 fetch()로 새로 읽어오므로, 파일/응답만
 *     최신이면 화면도 자동으로 최신 상태가 됩니다.
 *   - mock/meta.json 의 last_updated 값이 헤더의 "마지막 업데이트" 배지에 그대로
 *     표시되므로, 파이프라인이 이 값만 매일 갱신해줘도 "언제 기준 데이터인지"가
 *     사용자에게 보입니다.
 */
window.APP_CONFIG = {
  // true: mock/*.json 로컬 파일 사용, false: 실제 API 호출
  USE_MOCK: true,

  // 실제 백엔드 API의 베이스 URL (백엔드팀이 확정하면 채워주세요)
  API_BASE_URL: "https://REPLACE_ME.execute-api.ap-northeast-2.amazonaws.com/prod",

  // 각 화면이 필요로 하는 데이터의 엔드포인트 경로 (백엔드 API 완성 후 매핑)
  ENDPOINTS: {
    meta: "/meta",
    videoPool: "/videos/pool",
    channelPool: "/channels/pool",
    uploadHeatmap: "/gold/upload-strategy/heatmap",
    categoryTrend: "/gold/category-trend",
    metadataImpact: "/analysis/metadata-impact",
    syntheticDemo: "/analysis/synthetic-demo"
  },

  // mock 모드에서 사용할 로컬 파일 매핑
  MOCK_FILES: {
    meta: "mock/meta.json",
    videoPool: "mock/video_pool.json",
    channelPool: "mock/channel_pool.json",
    uploadHeatmap: "mock/upload_heatmap.json",
    categoryTrend: "mock/category_trend.json",
    // spec.md 분석 7(메타데이터 최적화) - frontend/scripts/build_metadata_impact.py가
    // outputs/silver/*.jsonl로 다중회귀(OLS)를 돌려서 생성
    metadataImpact: "mock/metadata_impact.json",
    // spec.md 분석 5/6(성장곡선 유형화 / 판단 시점) - 실제 시계열 데이터가 아직 부족해서
    // (2026-09-03 기준 실측 영상의 96%가 스냅샷 1개뿐) 합성(synthetic) 데이터로 기법만
    // 시연. frontend/scripts/build_synthetic_demo.py가 생성 - _is_synthetic:true 필수 확인
    syntheticDemo: "mock/synthetic_demo.json"
  },

  // 팀 확정 카테고리 (인물·블로그 제외, 2026-09-01 회의 기준)
  CATEGORIES: [
    { key: "gaming", category_id: "20", label: "게임", emoji: "🎮", colorVar: "--series-3" },
    { key: "autos_vehicles", category_id: "2", label: "자동차·차량", emoji: "🚗", colorVar: "--series-2" },
    { key: "film_animation", category_id: "1", label: "영화·애니메이션", emoji: "🎬", colorVar: "--series-1" }
  ],

  // 업로드 가이드 히트맵 - 요일(월~일, 개별) x 시간대 구간
  // 2026-09-02 기준: 파이프라인 gold_upload_strategy가 요일을 1~7(월~일)로 개별
  // 집계하므로, 프론트도 평일/토/일 3그룹이 아니라 요일별로 그대로 맞춥니다.
  DAY_GROUPS: ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"],
  TIME_SLOTS: ["새벽", "오전", "오후", "저녁", "심야"],

  // 추천 영상 분류 기준 (일 기준)
  TRENDING_MAX_DAYS: 90,   // 최근(트렌드) 영상: 게시 90일 이내
  STEADY_MIN_DAYS: 180     // 꾸준한(스테디) 영상: 게시 180일 이상
};
