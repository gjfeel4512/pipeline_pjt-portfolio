-- Silver(vw_video_analysis) -> Gold 집계
-- 실행: psql -d <database_name> -f compute_gold.sql
-- 이번 주 월요일을 analysis_week로 사용. 재실행해도 같은 주는 덮어씀(ON CONFLICT).

BEGIN;
SET search_path TO youtube_analytics, public;

-- 이번 실행에서 쓸 analysis_week (이번 주 월요일)를 임시 테이블로 고정
CREATE TEMP TABLE _run AS
SELECT date_trunc('week', CURRENT_DATE)::date AS analysis_week;

-- 최소 표본 수 (youtube_pipeline_schema_notes.md 기준: 30건 미만이면 추천 후보 제외)
CREATE TEMP TABLE _config AS SELECT 30 AS min_sample_count;

-- ============================================================
-- 1. gold_category_benchmark
-- ============================================================
WITH by_time AS (
    SELECT category_id, upload_time_bucket,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY views_per_day) AS med_views
    FROM vw_video_analysis
    GROUP BY category_id, upload_time_bucket
),
best_time AS (
    SELECT DISTINCT ON (category_id) category_id, upload_time_bucket
    FROM by_time ORDER BY category_id, med_views DESC NULLS LAST
),
by_duration AS (
    SELECT category_id, duration_bucket,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY views_per_day) AS med_views
    FROM vw_video_analysis
    GROUP BY category_id, duration_bucket
),
best_duration AS (
    SELECT DISTINCT ON (category_id) category_id, duration_bucket
    FROM by_duration ORDER BY category_id, med_views DESC NULLS LAST
),
by_type AS (
    SELECT s.category_id, s.video_type,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY v.views_per_day) AS med_views
    FROM fact_video_snapshot s
    JOIN vw_video_analysis v ON v.snapshot_id = s.snapshot_id
    GROUP BY s.category_id, s.video_type
),
best_type AS (
    SELECT DISTINCT ON (category_id) category_id, video_type
    FROM by_type ORDER BY category_id, med_views DESC NULLS LAST
),
agg AS (
    SELECT
        category_id,
        COUNT(*) AS sample_video_count,
        COUNT(DISTINCT channel_id) AS sample_channel_count,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_seconds) AS median_duration_seconds,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY views_per_day) AS median_views_per_day,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY like_rate) AS median_like_rate
    FROM vw_video_analysis
    GROUP BY category_id
)
INSERT INTO gold_category_benchmark
    (analysis_week, category_id, sample_video_count, sample_channel_count,
     median_duration_seconds, median_views_per_day, median_like_rate,
     best_upload_time_bucket, best_duration_bucket, best_video_type)
SELECT
    (SELECT analysis_week FROM _run), agg.category_id, agg.sample_video_count, agg.sample_channel_count,
    agg.median_duration_seconds, agg.median_views_per_day, agg.median_like_rate,
    best_time.upload_time_bucket, best_duration.duration_bucket, best_type.video_type
FROM agg
LEFT JOIN best_time ON best_time.category_id = agg.category_id
LEFT JOIN best_duration ON best_duration.category_id = agg.category_id
LEFT JOIN best_type ON best_type.category_id = agg.category_id
ON CONFLICT (analysis_week, category_id) DO UPDATE SET
    sample_video_count = EXCLUDED.sample_video_count,
    sample_channel_count = EXCLUDED.sample_channel_count,
    median_duration_seconds = EXCLUDED.median_duration_seconds,
    median_views_per_day = EXCLUDED.median_views_per_day,
    median_like_rate = EXCLUDED.median_like_rate,
    best_upload_time_bucket = EXCLUDED.best_upload_time_bucket,
    best_duration_bucket = EXCLUDED.best_duration_bucket,
    best_video_type = EXCLUDED.best_video_type,
    created_at_utc = NOW();

-- ============================================================
-- 2. gold_upload_strategy
-- ============================================================
WITH cell AS (
    SELECT
        s.category_id,
        (CASE
            WHEN s.subscriber_count_at_collection IS NULL THEN 'hidden_or_unknown'
            WHEN s.subscriber_count_at_collection < 1000 THEN 'new'
            WHEN s.subscriber_count_at_collection < 10000 THEN 'early_growth'
            WHEN s.subscriber_count_at_collection < 100000 THEN 'growth'
            ELSE 'established'
        END) AS subscriber_segment,
        s.video_type,
        v.duration_bucket,
        v.published_day_of_week,
        v.upload_time_bucket,
        v.views_per_day,
        v.like_rate,
        v.comment_rate
    FROM fact_video_snapshot s
    JOIN vw_video_analysis v ON v.snapshot_id = s.snapshot_id
),
agg AS (
    SELECT
        category_id, subscriber_segment, video_type, duration_bucket,
        published_day_of_week, upload_time_bucket,
        COUNT(*) AS sample_video_count,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY views_per_day) AS median_views_per_day,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY views_per_day) AS p75_views_per_day,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY like_rate) AS median_like_rate,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY comment_rate) AS median_comment_rate
    FROM cell
    GROUP BY category_id, subscriber_segment, video_type, duration_bucket,
             published_day_of_week, upload_time_bucket
),
ranked AS (
    SELECT *,
        RANK() OVER (
            PARTITION BY category_id, subscriber_segment
            ORDER BY median_views_per_day DESC NULLS LAST
        ) AS strategy_rank
    FROM agg
)
INSERT INTO gold_upload_strategy
    (analysis_week, category_id, subscriber_segment, video_type, duration_bucket,
     published_day_of_week, upload_time_bucket, sample_video_count,
     median_views_per_day, p75_views_per_day, median_like_rate, median_comment_rate,
     median_views_per_subscriber, strategy_rank, is_recommended)
SELECT
    (SELECT analysis_week FROM _run), category_id, subscriber_segment, video_type, duration_bucket,
    published_day_of_week, upload_time_bucket, sample_video_count,
    median_views_per_day, p75_views_per_day, median_like_rate, median_comment_rate,
    NULL, strategy_rank,
    (sample_video_count >= (SELECT min_sample_count FROM _config))
FROM ranked
ON CONFLICT (analysis_week, category_id, subscriber_segment, video_type,
             duration_bucket, published_day_of_week, upload_time_bucket) DO UPDATE SET
    sample_video_count = EXCLUDED.sample_video_count,
    median_views_per_day = EXCLUDED.median_views_per_day,
    p75_views_per_day = EXCLUDED.p75_views_per_day,
    median_like_rate = EXCLUDED.median_like_rate,
    median_comment_rate = EXCLUDED.median_comment_rate,
    strategy_rank = EXCLUDED.strategy_rank,
    is_recommended = EXCLUDED.is_recommended,
    created_at_utc = NOW();

-- ============================================================
-- 3. gold_new_creator_guide (신규/초기성장 채널 대상 상위 1개 조합)
-- ============================================================
WITH candidate AS (
    SELECT g.*,
        ROW_NUMBER() OVER (
            PARTITION BY g.category_id, g.subscriber_segment
            ORDER BY g.median_views_per_day DESC NULLS LAST
        ) AS rn
    FROM gold_upload_strategy g
    WHERE g.analysis_week = (SELECT analysis_week FROM _run)
      AND g.subscriber_segment IN ('new', 'early_growth')
      AND g.is_recommended = TRUE
),
top1 AS (
    SELECT * FROM candidate WHERE rn = 1
)
INSERT INTO gold_new_creator_guide
    (guide_id, analysis_week, category_id, target_creator_segment,
     recommended_video_type, recommended_duration_bucket, recommended_day_of_week,
     recommended_time_bucket, evidence_video_count, evidence_median_views_per_day,
     evidence_median_like_rate, guide_message, caveat)
SELECT
    gen_random_uuid(), (SELECT analysis_week FROM _run), t.category_id, t.subscriber_segment,
    t.video_type, t.duration_bucket, t.published_day_of_week, t.upload_time_bucket,
    t.sample_video_count, t.median_views_per_day, t.median_like_rate,
    format(
        '%s 카테고리의 %s 채널은 %s · %s 길이 · %s요일 %s 시간대 조합에서 표본 %s건 기준 중앙 일평균 조회수(%s)와 좋아요율(%s)이 상대적으로 높게 나타났습니다.',
        c.category_name_ko, t.subscriber_segment, t.video_type, t.duration_bucket,
        (ARRAY['월','화','수','목','금','토','일'])[t.published_day_of_week],
        t.upload_time_bucket, t.sample_video_count,
        ROUND(t.median_views_per_day, 1), ROUND(t.median_like_rate, 6)
    ),
    '이 결과는 수집 표본에 기반한 상관관계이며, 업로드 조건만 바꾼다고 성과가 보장되는 인과관계가 아닙니다.'
FROM top1 t
JOIN dim_category c ON c.category_id = t.category_id
ON CONFLICT (analysis_week, category_id, target_creator_segment) DO UPDATE SET
    recommended_video_type = EXCLUDED.recommended_video_type,
    recommended_duration_bucket = EXCLUDED.recommended_duration_bucket,
    recommended_day_of_week = EXCLUDED.recommended_day_of_week,
    recommended_time_bucket = EXCLUDED.recommended_time_bucket,
    evidence_video_count = EXCLUDED.evidence_video_count,
    evidence_median_views_per_day = EXCLUDED.evidence_median_views_per_day,
    evidence_median_like_rate = EXCLUDED.evidence_median_like_rate,
    guide_message = EXCLUDED.guide_message,
    created_at_utc = NOW();

-- 2026-09-03: 랭크 추적(gold_video_rank_trend) 기능 폐지로 Section 4 제거함.
-- (trending_rank 기반 순위 변화 집계는 더 이상 사용하지 않음)

COMMIT;
