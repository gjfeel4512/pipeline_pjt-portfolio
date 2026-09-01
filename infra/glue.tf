resource "aws_glue_catalog_database" "pipeline" {
  name = "${replace(local.resource_prefix, "-", "_")}_db"
}

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

    # 영상(video) 필드
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
      name = "channel_id"
      type = "string"
    }
    columns {
      name = "channel_title"
      type = "string"
    }
    columns {
      name = "duration"
      type = "string"
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
      name = "definition"
      type = "string"
    }
    columns {
      name = "caption"
      type = "string"
    }
    columns {
      name = "licensed_content"
      type = "boolean"
    }
    columns {
      name = "channel_view_count"
      type = "bigint"
    }
    columns {
      name = "subscriber_count"
      type = "bigint"
    }
    columns {
      name = "video_count"
      type = "bigint"
    }
    columns {
      name = "country"
      type = "string"
    }
    columns {
      name = "custom_url"
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