resource "aws_glue_catalog_database" "pipeline" {
  name = "${replace(local.resource_prefix, "-", "_")}_db"
}

# ==============================================================================
# Bronze 테이블: 실제 YouTube API 원본 응답 스키마 (youtube_api_collector.py 수집 결과)
# 주의: view_count/like_count/comment_count/subscriber_count 등은 YouTube API가
#       문자열로 반환하므로 Glue 컬럼 타입도 string으로 정의 (Silver에서 정수 변환)
# ==============================================================================
resource "aws_glue_catalog_table" "bronze_youtube" {
  name          = "bronze_youtube"
  database_name = aws_glue_catalog_database.pipeline.name
  table_type    = "EXTERNAL_TABLE"

  # 파티션 프로젝션: Glue Crawler나 MSCK REPAIR TABLE 없이 Athena가 category/year/month/day
  # 조합으로 파티션 위치를 계산식으로 알아냄 - 새 파티션(날짜)이 생겨도 등록 작업 불필요.
  # (2026-09-03: 파티션 미등록으로 Athena가 0건만 반환하던 문제 수정 - ARCHITECTURE_NOTE.md 참고)
  parameters = {
    EXTERNAL                     = "TRUE"
    "classification"             = "json"
    "compressionType"            = "gzip"
    "projection.enabled"         = "true"
    "projection.category.type"   = "enum"
    "projection.category.values" = "film_animation,autos_vehicles,gaming,people_blogs"
    "projection.year.type"       = "integer"
    # 파티션 프로젝션은 range 안의 모든 조합(year x month x day)에 대해 S3 LIST를
    # 날려 파일 존재를 확인한다 - 파티션 술어 없는 쿼리(gold_compute_athena.py)는
    # 2025~2030 x 1~12 x 1~31 = 8,928개 prefix를 매번 뒤졌고, 그게 9월 S3 요금의
    # 거의 전부였다(USW2-Requests-Tier1 ~85만건 = $4.3). 곧 teardown이라 실제 존재
    # 구간으로 좁힌다: Bronze는 month=08/day=31 ~ month=09/day=04, Silver는 09/02~04.
    # month.range x day.range는 카테시안 곱이라 "8월은 25~31, 9월은 1~9"처럼 월별로
    # 다른 day 범위를 못 준다 -> day는 1,31 통으로 둔다. 8,928 -> 4x1x2x31 = 248 (~36x).
    # 주의: 10월 이후로 넘어가면 month.range를 "8,10" 등으로 넓혀야 함(안 그러면
    # 그달 데이터가 프로젝션에서 빠져 쿼리에 안 잡힘).
    "projection.year.range"     = "2026,2026"
    "projection.month.type"     = "integer"
    "projection.month.range"    = "8,9"
    "projection.month.digits"   = "2"
    "projection.day.type"       = "integer"
    "projection.day.range"      = "1,31"
    "projection.day.digits"     = "2"
    "storage.location.template" = "s3://${aws_s3_bucket.bronze.id}/youtube/bronze/category=$${category}/year=$${year}/month=$${month}/day=$${day}/"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.bronze.id}/youtube/bronze/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }

    columns {
      name = "category_name"
      type = "string"
    }
    columns {
      name = "category_id"
      type = "string"
    }
    columns {
      name = "video_id"
      type = "string"
    }
    columns {
      name = "title"
      type = "string"
    }
    columns {
      name = "description"
      type = "string"
    }
    columns {
      name = "published_at"
      type = "string"
    }
    columns {
      name = "tags"
      type = "array<string>"
    }
    columns {
      name = "live_broadcast_content"
      type = "string"
    }
    columns {
      name = "view_count"
      type = "string"
    }
    columns {
      name = "like_count"
      type = "string"
    }
    columns {
      name = "comment_count"
      type = "string"
    }
    columns {
      name = "duration"
      type = "string"
    }
    columns {
      name = "definition"
      type = "string"
    }
    columns {
      name = "caption"
      type = "string"
    }
    columns {
      name = "privacy_status"
      type = "string"
    }
    columns {
      name = "channel_id"
      type = "string"
    }
    columns {
      name = "channel_name"
      type = "string"
    }
    columns {
      name = "channel_published_at"
      type = "string"
    }
    columns {
      name = "subscriber_count"
      type = "string"
    }
    columns {
      name = "hidden_subscriber_count"
      type = "boolean"
    }
    columns {
      name = "channel_total_view_count"
      type = "string"
    }
    columns {
      name = "channel_total_video_count"
      type = "string"
    }
    columns {
      name = "collected_at_utc"
      type = "string"
    }
  }

  partition_keys {
    name = "category"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }
}

# ==============================================================================
# Silver 테이블: Bronze -> Silver 변환 후 스키마 (dags/bronze_to_silver_dag_aws.py 의
# transform_to_silver() 출력과 1:1 대응)
# ==============================================================================
resource "aws_glue_catalog_table" "silver_youtube" {
  name          = "silver_youtube"
  database_name = aws_glue_catalog_database.pipeline.name
  table_type    = "EXTERNAL_TABLE"

  # 파티션 프로젝션(bronze_youtube 상단 주석 참고)
  parameters = {
    EXTERNAL                     = "TRUE"
    "classification"             = "json"
    "projection.enabled"         = "true"
    "projection.category.type"   = "enum"
    "projection.category.values" = "film_animation,autos_vehicles,gaming,people_blogs"
    "projection.year.type"       = "integer"
    # 파티션 프로젝션은 range 안의 모든 조합(year x month x day)에 대해 S3 LIST를
    # 날려 파일 존재를 확인한다 - 파티션 술어 없는 쿼리(gold_compute_athena.py)는
    # 2025~2030 x 1~12 x 1~31 = 8,928개 prefix를 매번 뒤졌고, 그게 9월 S3 요금의
    # 거의 전부였다(USW2-Requests-Tier1 ~85만건 = $4.3). 곧 teardown이라 실제 존재
    # 구간으로 좁힌다: Bronze는 month=08/day=31 ~ month=09/day=04, Silver는 09/02~04.
    # month.range x day.range는 카테시안 곱이라 "8월은 25~31, 9월은 1~9"처럼 월별로
    # 다른 day 범위를 못 준다 -> day는 1,31 통으로 둔다. 8,928 -> 4x1x2x31 = 248 (~36x).
    # 주의: 10월 이후로 넘어가면 month.range를 "8,10" 등으로 넓혀야 함(안 그러면
    # 그달 데이터가 프로젝션에서 빠져 쿼리에 안 잡힘).
    "projection.year.range"     = "2026,2026"
    "projection.month.type"     = "integer"
    "projection.month.range"    = "8,9"
    "projection.month.digits"   = "2"
    "projection.day.type"       = "integer"
    "projection.day.range"      = "1,31"
    "projection.day.digits"     = "2"
    "storage.location.template" = "s3://${aws_s3_bucket.silver.id}/youtube/silver/category=$${category}/year=$${year}/month=$${month}/day=$${day}/"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.silver.id}/youtube/silver/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }

    columns {
      name = "video_id"
      type = "string"
    }
    columns {
      name = "title"
      type = "string"
    }
    columns {
      name = "description"
      type = "string"
    }
    columns {
      name = "channel_id"
      type = "string"
    }
    columns {
      name = "channel_name"
      type = "string"
    }
    columns {
      name = "category_id"
      type = "int"
    }
    columns {
      name = "category_name"
      type = "string"
    }
    columns {
      name = "category_slug"
      type = "string"
    }
    columns {
      name = "tags"
      type = "array<string>"
    }
    columns {
      name = "matched_tags"
      type = "array<string>"
    }
    columns {
      name = "topic_categories"
      type = "array<string>"
    }
    columns {
      name = "view_count"
      type = "bigint"
    }
    columns {
      name = "like_count"
      type = "bigint"
    }
    columns {
      name = "comment_count"
      type = "bigint"
    }
    columns {
      name = "engagement_rate"
      type = "double"
    }
    columns {
      name = "duration_iso8601"
      type = "string"
    }
    columns {
      name = "duration_seconds"
      type = "int"
    }
    columns {
      name = "video_type"
      type = "string"
    }
    columns {
      name = "definition"
      type = "string"
    }
    columns {
      name = "is_hd"
      type = "boolean"
    }
    columns {
      name = "caption_available"
      type = "boolean"
    }
    columns {
      name = "has_paid_product_placement"
      type = "boolean"
    }
    columns {
      name = "privacy_status"
      type = "string"
    }
    columns {
      name = "made_for_kids"
      type = "boolean"
    }
    columns {
      name = "live_broadcast_content"
      type = "string"
    }
    columns {
      name = "is_live_content"
      type = "boolean"
    }
    columns {
      name = "default_audio_language"
      type = "string"
    }
    columns {
      name = "thumbnail_url"
      type = "string"
    }
    columns {
      name = "published_at_utc"
      type = "string"
    }
    columns {
      name = "published_at_kst"
      type = "string"
    }
    columns {
      name = "published_year_month"
      type = "string"
    }
    columns {
      name = "channel_published_at_utc"
      type = "string"
    }
    columns {
      name = "subscriber_count"
      type = "bigint"
    }
    columns {
      name = "hidden_subscriber_count"
      type = "boolean"
    }
    columns {
      name = "channel_total_view_count"
      type = "bigint"
    }
    columns {
      name = "channel_total_video_count"
      type = "bigint"
    }
    columns {
      name = "uploads_playlist_id"
      type = "string"
    }
    columns {
      name = "channel_thumbnail_url"
      type = "string"
    }
    columns {
      name = "collected_at_utc"
      type = "string"
    }
    columns {
      name = "is_valid"
      type = "boolean"
    }
    columns {
      name = "silver_transformed_at_utc"
      type = "string"
    }
    columns {
      name = "source"
      type = "string"
    }
  }

  partition_keys {
    name = "category"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }
}

# ==============================================================================
# Silver-Rejected 테이블: 오염 데이터로 분리된 레코드 (category_id=22 '인물/블로그' 등)
# 감사/추적 목적으로 Athena에서 조회 가능
# ==============================================================================
resource "aws_glue_catalog_table" "silver_youtube_rejected" {
  name          = "silver_youtube_rejected"
  database_name = aws_glue_catalog_database.pipeline.name
  table_type    = "EXTERNAL_TABLE"

  # 파티션 프로젝션(bronze_youtube 상단 주석 참고)
  parameters = {
    EXTERNAL                     = "TRUE"
    "classification"             = "json"
    "projection.enabled"         = "true"
    "projection.category.type"   = "enum"
    "projection.category.values" = "film_animation,autos_vehicles,gaming,people_blogs"
    "projection.year.type"       = "integer"
    # 파티션 프로젝션은 range 안의 모든 조합(year x month x day)에 대해 S3 LIST를
    # 날려 파일 존재를 확인한다 - 파티션 술어 없는 쿼리(gold_compute_athena.py)는
    # 2025~2030 x 1~12 x 1~31 = 8,928개 prefix를 매번 뒤졌고, 그게 9월 S3 요금의
    # 거의 전부였다(USW2-Requests-Tier1 ~85만건 = $4.3). 곧 teardown이라 실제 존재
    # 구간으로 좁힌다: Bronze는 month=08/day=31 ~ month=09/day=04, Silver는 09/02~04.
    # month.range x day.range는 카테시안 곱이라 "8월은 25~31, 9월은 1~9"처럼 월별로
    # 다른 day 범위를 못 준다 -> day는 1,31 통으로 둔다. 8,928 -> 4x1x2x31 = 248 (~36x).
    # 주의: 10월 이후로 넘어가면 month.range를 "8,10" 등으로 넓혀야 함(안 그러면
    # 그달 데이터가 프로젝션에서 빠져 쿼리에 안 잡힘).
    "projection.year.range"     = "2026,2026"
    "projection.month.type"     = "integer"
    "projection.month.range"    = "8,9"
    "projection.month.digits"   = "2"
    "projection.day.type"       = "integer"
    "projection.day.range"      = "1,31"
    "projection.day.digits"     = "2"
    "storage.location.template" = "s3://${aws_s3_bucket.silver.id}/youtube/silver-rejected/category=$${category}/year=$${year}/month=$${month}/day=$${day}/"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.silver.id}/youtube/silver-rejected/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }

    columns {
      name = "video_id"
      type = "string"
    }
    columns {
      name = "title"
      type = "string"
    }
    columns {
      name = "description"
      type = "string"
    }
    columns {
      name = "channel_id"
      type = "string"
    }
    columns {
      name = "channel_name"
      type = "string"
    }
    columns {
      name = "category_id"
      type = "int"
    }
    columns {
      name = "category_name"
      type = "string"
    }
    columns {
      name = "category_slug"
      type = "string"
    }
    columns {
      name = "tags"
      type = "array<string>"
    }
    columns {
      name = "matched_tags"
      type = "array<string>"
    }
    columns {
      name = "topic_categories"
      type = "array<string>"
    }
    columns {
      name = "view_count"
      type = "bigint"
    }
    columns {
      name = "like_count"
      type = "bigint"
    }
    columns {
      name = "comment_count"
      type = "bigint"
    }
    columns {
      name = "engagement_rate"
      type = "double"
    }
    columns {
      name = "duration_iso8601"
      type = "string"
    }
    columns {
      name = "duration_seconds"
      type = "int"
    }
    columns {
      name = "video_type"
      type = "string"
    }
    columns {
      name = "definition"
      type = "string"
    }
    columns {
      name = "is_hd"
      type = "boolean"
    }
    columns {
      name = "caption_available"
      type = "boolean"
    }
    columns {
      name = "has_paid_product_placement"
      type = "boolean"
    }
    columns {
      name = "privacy_status"
      type = "string"
    }
    columns {
      name = "made_for_kids"
      type = "boolean"
    }
    columns {
      name = "live_broadcast_content"
      type = "string"
    }
    columns {
      name = "is_live_content"
      type = "boolean"
    }
    columns {
      name = "default_audio_language"
      type = "string"
    }
    columns {
      name = "thumbnail_url"
      type = "string"
    }
    columns {
      name = "published_at_utc"
      type = "string"
    }
    columns {
      name = "published_at_kst"
      type = "string"
    }
    columns {
      name = "published_year_month"
      type = "string"
    }
    columns {
      name = "channel_published_at_utc"
      type = "string"
    }
    columns {
      name = "subscriber_count"
      type = "bigint"
    }
    columns {
      name = "hidden_subscriber_count"
      type = "boolean"
    }
    columns {
      name = "channel_total_view_count"
      type = "bigint"
    }
    columns {
      name = "channel_total_video_count"
      type = "bigint"
    }
    columns {
      name = "uploads_playlist_id"
      type = "string"
    }
    columns {
      name = "channel_thumbnail_url"
      type = "string"
    }
    columns {
      name = "collected_at_utc"
      type = "string"
    }
    columns {
      name = "is_valid"
      type = "boolean"
    }
    columns {
      name = "silver_transformed_at_utc"
      type = "string"
    }
    columns {
      name = "source"
      type = "string"
    }
  }

  partition_keys {
    name = "category"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
  partition_keys {
    name = "day"
    type = "string"
  }
}

# ==============================================================================
# Gold 테이블 3개: transforms/export_gold_to_s3.py가 Postgres gold_* 테이블을
# analysis_week 파티션의 JSON Lines로 내보낸 결과 (sql/youtube_pipeline_schema_postgresql.sql
# 스키마와 1:1 대응). Postgres가 실제 조회 대상, 이 테이블들은 Athena로 팀 전체가
# 쿼리할 수 있게 하는 사본.
#
# 주의: export_gold_to_s3.py의 SELECT * 결과에서 analysis_week 컬럼은 파티션 키와
# 중복되므로 export 시 제거됨 - 그래서 아래 columns 블록엔 analysis_week이 없음
# (partition_keys에만 있음). 데이터 파일에 파티션 컬럼이 중복으로 들어있으면 Athena가
# 그 파일을 조용히 무시해서 쿼리 결과가 항상 0행이 되는 문제가 있었음(2026-09-03 확인).
#
# 파티션 프로젝션의 interval.unit은 WEEKS가 아니라 DAYS를 씀 - analysis_week 값이
# 항상 월요일인데, WEEKS 간격을 임의 시작일부터 생성하면 요일이 안 맞아서 절대
# 매치가 안 되는 문제가 있었음(2026-09-03 확인). DAYS로 매일 후보를 만들면 이 정렬
# 문제 자체가 없음.
# ==============================================================================
resource "aws_glue_catalog_table" "gold_category_benchmark" {
  name          = "gold_category_benchmark"
  database_name = aws_glue_catalog_database.pipeline.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL                                 = "TRUE"
    "classification"                         = "json"
    "projection.enabled"                     = "true"
    "projection.analysis_week.type"          = "date"
    "projection.analysis_week.format"        = "yyyy-MM-dd"
    "projection.analysis_week.range"         = "2026-08-01,NOW" # 존재 구간만 (원래 2026-01-01,NOW). 실제 데이터는 analysis_week=2026-08-31 (그 주 월요일) 하나뿐 - 시작을 09-01로 잡으면 배제되므로 08-01부터.
    "projection.analysis_week.interval"      = "1"
    "projection.analysis_week.interval.unit" = "DAYS"
    "storage.location.template"              = "s3://${aws_s3_bucket.gold.id}/gold_category_benchmark/analysis_week=$${analysis_week}/"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.gold.id}/gold_category_benchmark/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }

    columns {
      name = "category_id"
      type = "string"
    }
    columns {
      name = "sample_video_count"
      type = "int"
    }
    columns {
      name = "sample_channel_count"
      type = "int"
    }
    columns {
      name = "median_duration_seconds"
      type = "double"
    }
    columns {
      name = "median_views_per_day"
      type = "double"
    }
    columns {
      name = "median_like_rate"
      type = "double"
    }
    columns {
      name = "best_upload_time_bucket"
      type = "string"
    }
    columns {
      name = "best_duration_bucket"
      type = "string"
    }
    columns {
      name = "best_video_type"
      type = "string"
    }
    columns {
      name = "created_at_utc"
      type = "string"
    }
  }

  partition_keys {
    name = "analysis_week"
    type = "string"
  }
}

resource "aws_glue_catalog_table" "gold_upload_strategy" {
  name          = "gold_upload_strategy"
  database_name = aws_glue_catalog_database.pipeline.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL                                 = "TRUE"
    "classification"                         = "json"
    "projection.enabled"                     = "true"
    "projection.analysis_week.type"          = "date"
    "projection.analysis_week.format"        = "yyyy-MM-dd"
    "projection.analysis_week.range"         = "2026-08-01,NOW" # 존재 구간만 (원래 2026-01-01,NOW). 실제 데이터는 analysis_week=2026-08-31 (그 주 월요일) 하나뿐 - 시작을 09-01로 잡으면 배제되므로 08-01부터.
    "projection.analysis_week.interval"      = "1"
    "projection.analysis_week.interval.unit" = "DAYS"
    "storage.location.template"              = "s3://${aws_s3_bucket.gold.id}/gold_upload_strategy/analysis_week=$${analysis_week}/"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.gold.id}/gold_upload_strategy/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }

    columns {
      name = "category_id"
      type = "string"
    }
    columns {
      name = "subscriber_segment"
      type = "string"
    }
    columns {
      name = "video_type"
      type = "string"
    }
    columns {
      name = "duration_bucket"
      type = "string"
    }
    columns {
      name = "published_day_of_week"
      type = "int"
    }
    columns {
      name = "upload_time_bucket"
      type = "string"
    }
    columns {
      name = "sample_video_count"
      type = "int"
    }
    columns {
      name = "median_views_per_day"
      type = "double"
    }
    columns {
      name = "p75_views_per_day"
      type = "double"
    }
    columns {
      name = "median_like_rate"
      type = "double"
    }
    columns {
      name = "median_comment_rate"
      type = "double"
    }
    columns {
      name = "median_views_per_subscriber"
      type = "double"
    }
    columns {
      name = "strategy_rank"
      type = "int"
    }
    columns {
      name = "is_recommended"
      type = "boolean"
    }
    columns {
      name = "created_at_utc"
      type = "string"
    }
  }

  partition_keys {
    name = "analysis_week"
    type = "string"
  }
}

resource "aws_glue_catalog_table" "gold_new_creator_guide" {
  name          = "gold_new_creator_guide"
  database_name = aws_glue_catalog_database.pipeline.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL                                 = "TRUE"
    "classification"                         = "json"
    "projection.enabled"                     = "true"
    "projection.analysis_week.type"          = "date"
    "projection.analysis_week.format"        = "yyyy-MM-dd"
    "projection.analysis_week.range"         = "2026-08-01,NOW" # 존재 구간만 (원래 2026-01-01,NOW). 실제 데이터는 analysis_week=2026-08-31 (그 주 월요일) 하나뿐 - 시작을 09-01로 잡으면 배제되므로 08-01부터.
    "projection.analysis_week.interval"      = "1"
    "projection.analysis_week.interval.unit" = "DAYS"
    "storage.location.template"              = "s3://${aws_s3_bucket.gold.id}/gold_new_creator_guide/analysis_week=$${analysis_week}/"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.gold.id}/gold_new_creator_guide/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
    }

    columns {
      name = "guide_id"
      type = "string"
    }
    columns {
      name = "category_id"
      type = "string"
    }
    columns {
      name = "target_creator_segment"
      type = "string"
    }
    columns {
      name = "recommended_video_type"
      type = "string"
    }
    columns {
      name = "recommended_duration_bucket"
      type = "string"
    }
    columns {
      name = "recommended_day_of_week"
      type = "int"
    }
    columns {
      name = "recommended_time_bucket"
      type = "string"
    }
    columns {
      name = "evidence_video_count"
      type = "int"
    }
    columns {
      name = "evidence_median_views_per_day"
      type = "double"
    }
    columns {
      name = "evidence_median_like_rate"
      type = "double"
    }
    columns {
      name = "guide_message"
      type = "string"
    }
    columns {
      name = "caveat"
      type = "string"
    }
    columns {
      name = "created_at_utc"
      type = "string"
    }
  }

  partition_keys {
    name = "analysis_week"
    type = "string"
  }
}
