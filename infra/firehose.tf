# ==============================================================================
# Kinesis Data Firehose - Bronze 자동 적재 (동적 파티셔닝)
# ==============================================================================

resource "aws_cloudwatch_log_group" "firehose" {
  name              = "/aws/firehose/${local.resource_prefix}-bronze"
  retention_in_days = 30
  tags              = local.common_tags
}

resource "aws_cloudwatch_log_stream" "firehose" {
  name           = "S3Delivery"
  log_group_name = aws_cloudwatch_log_group.firehose.name
}

resource "aws_iam_role" "firehose_role" {
  name = "${local.resource_prefix}-firehose-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "firehose.amazonaws.com" }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "firehose_policy" {
  name = "${local.resource_prefix}-firehose-policy"
  role = aws_iam_role.firehose_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:AbortMultipartUpload",
          "s3:GetBucketLocation",
          "s3:GetObject",
          "s3:ListBucket",
          "s3:ListBucketMultipartUploads",
          "s3:PutObject"
        ]
        Resource = [
          aws_s3_bucket.bronze.arn,
          "${aws_s3_bucket.bronze.arn}/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.firehose.arn}:*"]
      },
      {
        Effect   = "Allow"
        Action   = ["glue:GetTable", "glue:GetTableVersion", "glue:GetTableVersions"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_kinesis_firehose_delivery_stream" "bronze" {
  name        = "${local.resource_prefix}-bronze-stream"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.firehose_role.arn
    bucket_arn = aws_s3_bucket.bronze.arn

    buffering_size     = 64   # MB (동적 파티셔닝 사용시 최소 권장값)
    buffering_interval  = 60   # 초
    compression_format  = "GZIP"

    # year/month/day는 !{timestamp:...}가 아니라 !{partitionKeyFromQuery:...}를 쓴다 -
    # !{timestamp:...}는 Firehose 도착 시각 기준이라 항상 UTC로 고정되어 KST와
    # 어긋나므로, 프로듀서(transforms/push_to_firehose.py,
    # dags/bronze_to_silver_dag_aws.py)가 레코드에 실어 보내는 KST 날짜를 그대로 쓴다.
    prefix              = "youtube/bronze/category=!{partitionKeyFromQuery:category}/year=!{partitionKeyFromQuery:year}/month=!{partitionKeyFromQuery:month}/day=!{partitionKeyFromQuery:day}/"
    error_output_prefix = "youtube/bronze-errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"

    dynamic_partitioning_configuration {
      enabled = true
    }

    processing_configuration {
      enabled = true

      processors {
        type = "MetadataExtraction"
        parameters {
          parameter_name  = "MetadataExtractionQuery"
          parameter_value = "{category:.category, year:.year_kst, month:.month_kst, day:.day_kst}"
        }
        parameters {
          parameter_name  = "JsonParsingEngine"
          parameter_value = "JQ-1.6"
        }
      }

      processors {
        type = "AppendDelimiterToRecord"
        parameters {
          parameter_name  = "Delimiter"
          parameter_value = "\\n"
        }
      }
    }

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose.name
      log_stream_name = aws_cloudwatch_log_stream.firehose.name
    }
  }

  tags = local.common_tags
}