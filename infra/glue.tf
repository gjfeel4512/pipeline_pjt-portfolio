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

  parameters = {
    EXTERNAL          = "TRUE"
    "classification"  = "json"
    "compressionType" = "gzip"
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

  parameters = {
    EXTERNAL         = "TRUE"
    "classification" = "json"
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
        name = "privacy_status"
        type = "string"
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

  parameters = {
    EXTERNAL         = "TRUE"
    "classification" = "json"
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
        name = "privacy_status"
        type = "string"
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
