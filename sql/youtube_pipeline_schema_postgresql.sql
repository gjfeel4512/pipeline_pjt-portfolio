-- YouTube 유튜버 가이드 파이프라인 스키마
-- DBMS: PostgreSQL 15+ 기준
-- 실행: psql -d <database_name> -f youtube_pipeline_schema_postgresql.sql
-- 설계 배경: ../youtube_pipeline_schema_notes.md 참조

BEGIN;

CREATE SCHEMA IF NOT EXISTS youtube_analytics;
SET search_path TO youtube_analytics, public;

-- ============================================================
-- 1. Bronze: YouTube API 원본 응답 보관
-- ============================================================
CREATE TABLE IF NOT EXISTS bronze_api_response (
    ingestion_id         UUID NOT NULL,
    collected_at_utc     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    collected_date       DATE NOT NULL,
    api_resource         VARCHAR(20) NOT NULL,
    category_id          VARCHAR(10),
    request_params_json  JSONB NOT NULL DEFAULT '{}'::JSONB,
    response_json        JSONB NOT NULL,
    http_status          INTEGER NOT NULL,
    page_token           TEXT,
    source_file_path     TEXT,
    created_at_utc       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_bronze_api_response
        PRIMARY KEY (ingestion_id, api_resource, collected_at_utc),
    CONSTRAINT ck_bronze_api_resource
        CHECK (api_resource IN ('search', 'videos', 'channels')),
    CONSTRAINT ck_bronze_http_status
        CHECK (http_status BETWEEN 100 AND 599)
);

CREATE INDEX IF NOT EXISTS idx_bronze_collected_resource
    ON bronze_api_response (collected_date, api_resource, category_id);

-- ============================================================
-- 2. Silver Dimension Tables
-- ============================================================
CREATE TABLE IF NOT EXISTS dim_category (
    category_id       VARCHAR(10) PRIMARY KEY,
    category_name_ko  VARCHAR(50) NOT NULL,
    category_name_en  VARCHAR(100),
    is_target         BOOLEAN NOT NULL DEFAULT TRUE
);

INSERT INTO dim_category (category_id, category_name_ko, category_name_en)
VALUES
    ('1',  '영화·애니메이션', 'Film & Animation'),
    ('2',  '자동차·차량',     'Autos & Vehicles'),
    ('20', '게임',            'Gaming'),
    ('22', '인물·블로그',     'People & Blogs')
ON CONFLICT (category_id) DO UPDATE
SET category_name_ko = EXCLUDED.category_name_ko,
    category_name_en = EXCLUDED.category_name_en,
    is_target = TRUE;

CREATE TABLE IF NOT EXISTS dim_channel (
    channel_id                VARCHAR(64) PRIMARY KEY,
    channel_title             TEXT NOT NULL,
    channel_published_at      TIMESTAMPTZ,
    subscriber_count          BIGINT,
    hidden_subscriber_count   BOOLEAN NOT NULL DEFAULT FALSE,
    channel_view_count        BIGINT,
    channel_video_count       INTEGER,
    uploads_playlist_id       VARCHAR(64),  -- channels.list contentDetails.relatedPlaylists.uploads - playlistItems.list로 신규 업로드를 저비용(1유닛)으로 체크하는 데 씀
    last_collected_at_utc     TIMESTAMPTZ NOT NULL,
    created_at_utc            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc            TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_channel_subscriber_count CHECK (subscriber_count IS NULL OR subscriber_count >= 0),
    CONSTRAINT ck_channel_view_count CHECK (channel_view_count IS NULL OR channel_view_count >= 0),
    CONSTRAINT ck_channel_video_count CHECK (channel_video_count IS NULL OR channel_video_count >= 0)
);

CREATE TABLE IF NOT EXISTS dim_date (
    date_key            DATE PRIMARY KEY,
    year                SMALLINT NOT NULL,
    month               SMALLINT NOT NULL,
    day                 SMALLINT NOT NULL,
    day_of_week_num     SMALLINT NOT NULL,
    day_of_week_ko      VARCHAR(10) NOT NULL,
    is_weekend          BOOLEAN NOT NULL,

    CONSTRAINT ck_date_month CHECK (month BETWEEN 1 AND 12),
    CONSTRAINT ck_date_day CHECK (day BETWEEN 1 AND 31),
    CONSTRAINT ck_date_dow CHECK (day_of_week_num BETWEEN 1 AND 7)
);

-- ============================================================
-- 3. Silver Fact: 영상 1건 x 수집일 1건의 통계 스냅샷
-- ============================================================
CREATE TABLE IF NOT EXISTS fact_video_snapshot (
    snapshot_id                     UUID PRIMARY KEY,
    video_id                        VARCHAR(32) NOT NULL,
    channel_id                      VARCHAR(64) NOT NULL,
    category_id                     VARCHAR(10) NOT NULL,

    published_at_utc                TIMESTAMPTZ NOT NULL,
    published_at_kst                TIMESTAMP NOT NULL,
    published_date_kst              DATE NOT NULL,
    published_hour_kst              SMALLINT NOT NULL,
    published_day_of_week           SMALLINT NOT NULL,

    title                           TEXT NOT NULL,
    duration_iso8601                VARCHAR(32) NOT NULL,
    duration_seconds                INTEGER NOT NULL,
    video_type                      VARCHAR(20) NOT NULL,
    definition                      VARCHAR(10),
    caption_available               BOOLEAN,
    live_broadcast_content          VARCHAR(20) NOT NULL DEFAULT 'none',
    default_audio_language          VARCHAR(10),
    thumbnail_url                   TEXT,
    made_for_kids                   BOOLEAN,
    has_paid_product_placement      BOOLEAN NOT NULL DEFAULT FALSE,
    topic_categories                TEXT[],

    view_count                      BIGINT NOT NULL,
    like_count                      BIGINT,
    comment_count                   BIGINT,
    subscriber_count_at_collection  BIGINT,
    trending_rank                   SMALLINT,  -- chart=mostPopular 응답 순서(1위=가장 인기). 배치(search 기반) 수집 레코드는 NULL

    collected_at_utc                TIMESTAMPTZ NOT NULL,
    collected_date                  DATE NOT NULL,
    is_public                       BOOLEAN NOT NULL DEFAULT TRUE,
    is_valid                        BOOLEAN NOT NULL DEFAULT TRUE,
    invalid_reason                  TEXT,
    created_at_utc                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_video_snapshot UNIQUE (video_id, collected_date),
    CONSTRAINT fk_snapshot_channel FOREIGN KEY (channel_id) REFERENCES dim_channel(channel_id),
    CONSTRAINT fk_snapshot_category FOREIGN KEY (category_id) REFERENCES dim_category(category_id),
    CONSTRAINT fk_snapshot_date FOREIGN KEY (published_date_kst) REFERENCES dim_date(date_key),
    CONSTRAINT ck_snapshot_hour CHECK (published_hour_kst BETWEEN 0 AND 23),
    CONSTRAINT ck_snapshot_dow CHECK (published_day_of_week BETWEEN 1 AND 7),
    CONSTRAINT ck_snapshot_duration CHECK (duration_seconds >= 0),
    CONSTRAINT ck_snapshot_video_type CHECK (video_type IN ('shorts', 'short_form', 'long_form')),
    CONSTRAINT ck_snapshot_definition CHECK (definition IS NULL OR definition IN ('hd', 'sd')),
    CONSTRAINT ck_snapshot_view_count CHECK (view_count >= 0),
    CONSTRAINT ck_snapshot_like_count CHECK (like_count IS NULL OR like_count >= 0),
    CONSTRAINT ck_snapshot_comment_count CHECK (comment_count IS NULL OR comment_count >= 0),
    CONSTRAINT ck_snapshot_subscriber_count CHECK (subscriber_count_at_collection IS NULL OR subscriber_count_at_collection >= 0),
    CONSTRAINT ck_snapshot_trending_rank CHECK (trending_rank IS NULL OR trending_rank >= 1)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_category_published
    ON fact_video_snapshot (category_id, published_date_kst);
CREATE INDEX IF NOT EXISTS idx_snapshot_channel_collected
    ON fact_video_snapshot (channel_id, collected_date DESC);
CREATE INDEX IF NOT EXISTS idx_snapshot_collected_date
    ON fact_video_snapshot (collected_date);

-- ============================================================
-- 4. Silver View: 분석 파생 지표
-- ============================================================
CREATE OR REPLACE VIEW vw_video_analysis AS
SELECT
    s.snapshot_id,
    s.video_id,
    s.channel_id,
    s.category_id,
    c.category_name_ko,
    s.title,
    s.published_at_utc,
    s.published_at_kst,
    s.published_date_kst,
    s.published_hour_kst,
    s.published_day_of_week,
    CASE s.published_day_of_week
        WHEN 1 THEN '월' WHEN 2 THEN '화' WHEN 3 THEN '수' WHEN 4 THEN '목'
        WHEN 5 THEN '금' WHEN 6 THEN '토' WHEN 7 THEN '일'
    END AS published_day_of_week_ko,
    s.duration_seconds,
    s.video_type,
    s.view_count,
    s.like_count,
    s.comment_count,
    s.subscriber_count_at_collection,
    s.collected_at_utc,
    s.collected_date,
    GREATEST(1, FLOOR(EXTRACT(EPOCH FROM (s.collected_at_utc - s.published_at_utc)) / 86400)::INTEGER) AS video_age_days,
    ROUND(s.view_count::NUMERIC / GREATEST(1, FLOOR(EXTRACT(EPOCH FROM (s.collected_at_utc - s.published_at_utc)) / 86400)::INTEGER), 4) AS views_per_day,
    ROUND(s.like_count::NUMERIC / NULLIF(s.view_count, 0), 6) AS like_rate,
    ROUND(s.comment_count::NUMERIC / NULLIF(s.view_count, 0), 6) AS comment_rate,
    ROUND(s.view_count::NUMERIC / NULLIF(s.subscriber_count_at_collection, 0), 6) AS views_per_subscriber,
    CASE
        WHEN s.subscriber_count_at_collection IS NULL THEN 'hidden_or_unknown'
        WHEN s.subscriber_count_at_collection < 1000 THEN 'new'
        WHEN s.subscriber_count_at_collection < 10000 THEN 'early_growth'
        WHEN s.subscriber_count_at_collection < 100000 THEN 'growth'
        ELSE 'established'
    END AS subscriber_segment,
    CASE
        WHEN s.duration_seconds <= 60 THEN 'shorts'
        WHEN s.duration_seconds <= 300 THEN '1_to_5m'
        WHEN s.duration_seconds <= 600 THEN '5_to_10m'
        WHEN s.duration_seconds <= 1200 THEN '10_to_20m'
        ELSE '20m_plus'
    END AS duration_bucket,
    CASE
        WHEN s.published_hour_kst BETWEEN 0 AND 5 THEN 'dawn'
        WHEN s.published_hour_kst BETWEEN 6 AND 11 THEN 'morning'
        WHEN s.published_hour_kst BETWEEN 12 AND 17 THEN 'afternoon'
        WHEN s.published_hour_kst BETWEEN 18 AND 21 THEN 'evening'
        ELSE 'night'
    END AS upload_time_bucket
FROM fact_video_snapshot s
JOIN dim_category c ON c.category_id = s.category_id
WHERE s.is_public = TRUE
  AND s.is_valid = TRUE;

-- ============================================================
-- 5. Gold Tables
-- ============================================================
CREATE TABLE IF NOT EXISTS gold_upload_strategy (
    analysis_week                 DATE NOT NULL,
    category_id                   VARCHAR(10) NOT NULL,
    subscriber_segment            VARCHAR(30) NOT NULL,
    video_type                    VARCHAR(20) NOT NULL,
    duration_bucket               VARCHAR(20) NOT NULL,
    published_day_of_week         SMALLINT NOT NULL,
    upload_time_bucket            VARCHAR(20) NOT NULL,
    sample_video_count            INTEGER NOT NULL,
    median_views_per_day          NUMERIC(20,4),
    p75_views_per_day             NUMERIC(20,4),
    median_like_rate              NUMERIC(12,6),
    median_comment_rate           NUMERIC(12,6),
    median_views_per_subscriber   NUMERIC(16,6),
    strategy_rank                 INTEGER,
    is_recommended                BOOLEAN NOT NULL DEFAULT FALSE,
    created_at_utc                TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_gold_upload_strategy PRIMARY KEY (
        analysis_week, category_id, subscriber_segment, video_type,
        duration_bucket, published_day_of_week, upload_time_bucket
    ),
    CONSTRAINT fk_gold_strategy_category FOREIGN KEY (category_id) REFERENCES dim_category(category_id),
    CONSTRAINT ck_gold_strategy_sample_count CHECK (sample_video_count >= 0)
);

CREATE TABLE IF NOT EXISTS gold_category_benchmark (
    analysis_week               DATE NOT NULL,
    category_id                 VARCHAR(10) NOT NULL,
    sample_video_count          INTEGER NOT NULL,
    sample_channel_count        INTEGER NOT NULL,
    median_duration_seconds     NUMERIC(12,2),
    median_views_per_day        NUMERIC(20,4),
    median_like_rate            NUMERIC(12,6),
    best_upload_time_bucket     VARCHAR(20),
    best_duration_bucket        VARCHAR(20),
    best_video_type             VARCHAR(20),
    created_at_utc              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_gold_category_benchmark PRIMARY KEY (analysis_week, category_id),
    CONSTRAINT fk_gold_benchmark_category FOREIGN KEY (category_id) REFERENCES dim_category(category_id)
);

CREATE TABLE IF NOT EXISTS gold_video_rank_trend (
    video_id                      VARCHAR(32) PRIMARY KEY,
    category_id                   VARCHAR(10) NOT NULL,
    channel_id                    VARCHAR(64) NOT NULL,
    title                         TEXT NOT NULL,
    snapshot_count                INTEGER NOT NULL,

    first_seen_date               DATE NOT NULL,
    last_seen_date                DATE NOT NULL,
    first_rank                    SMALLINT NOT NULL,
    latest_rank                   SMALLINT NOT NULL,
    best_rank                     SMALLINT NOT NULL,
    rank_change                   SMALLINT NOT NULL,  -- first_rank - latest_rank (양수=순위 상승)
    trend_direction                VARCHAR(20) NOT NULL,  -- rising/falling/stable/insufficient_data

    first_view_count              BIGINT NOT NULL,
    latest_view_count             BIGINT NOT NULL,
    view_count_growth             BIGINT NOT NULL,
    view_count_growth_rate        NUMERIC(12,6),

    first_like_count              BIGINT,
    latest_like_count             BIGINT,
    like_count_growth             BIGINT,

    created_at_utc                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_gold_trend_category FOREIGN KEY (category_id) REFERENCES dim_category(category_id),
    CONSTRAINT fk_gold_trend_channel FOREIGN KEY (channel_id) REFERENCES dim_channel(channel_id),
    CONSTRAINT ck_gold_trend_direction CHECK (trend_direction IN ('rising', 'falling', 'stable', 'insufficient_data')),
    CONSTRAINT ck_gold_trend_snapshot_count CHECK (snapshot_count >= 1)
);

CREATE TABLE IF NOT EXISTS gold_new_creator_guide (
    guide_id                      UUID PRIMARY KEY,
    analysis_week                 DATE NOT NULL,
    category_id                   VARCHAR(10) NOT NULL,
    target_creator_segment        VARCHAR(30) NOT NULL DEFAULT 'new',
    recommended_video_type        VARCHAR(20) NOT NULL,
    recommended_duration_bucket   VARCHAR(20) NOT NULL,
    recommended_day_of_week       SMALLINT NOT NULL,
    recommended_time_bucket       VARCHAR(20) NOT NULL,
    evidence_video_count          INTEGER NOT NULL,
    evidence_median_views_per_day NUMERIC(20,4),
    evidence_median_like_rate     NUMERIC(12,6),
    guide_message                 TEXT NOT NULL,
    caveat                        TEXT NOT NULL,
    created_at_utc                TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_gold_creator_guide UNIQUE (analysis_week, category_id, target_creator_segment),
    CONSTRAINT fk_gold_creator_category FOREIGN KEY (category_id) REFERENCES dim_category(category_id),
    CONSTRAINT ck_gold_creator_sample_count CHECK (evidence_video_count >= 0)
);

COMMIT;
