# IAM 역할: Airflow용
resource "aws_iam_role" "airflow" {
  name = "${local.resource_prefix}-airflow-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "airflow.amazonaws.com"
          AWS     = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
      }
    ]
  })

  tags = local.common_tags
}

# IAM 정책: S3 접근 권한
resource "aws_iam_policy" "airflow_s3" {
  name        = "${local.resource_prefix}-airflow-s3-policy"
  description = "Airflow가 S3 버킷에 접근하기 위한 정책"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ListBuckets"
        Effect = "Allow"
        Action = [
          "s3:ListAllMyBuckets",
          "s3:GetBucketLocation"
        ]
        Resource = "*"
      },
      {
        Sid    = "BronzeBucketAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.bronze.arn,
          "${aws_s3_bucket.bronze.arn}/*"
        ]
      },
      {
        Sid    = "SilverBucketAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.silver.arn,
          "${aws_s3_bucket.silver.arn}/*"
        ]
      },
      {
        Sid    = "GoldBucketAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.gold.arn,
          "${aws_s3_bucket.gold.arn}/*"
        ]
      }
    ]
  })

  tags = local.common_tags
}

# IAM 정책: CloudWatch 로그 쓰기
resource "aws_iam_policy" "airflow_logs" {
  name        = "${local.resource_prefix}-airflow-logs-policy"
  description = "Airflow가 CloudWatch 로그에 쓰기 위한 정책"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CreateLogGroup"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/airflow/*"
      },
      {
        Sid    = "PutLogEvents"
        Effect = "Allow"
        Action = [
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/airflow/*:*"
      }
    ]
  })

  tags = local.common_tags
}

# IAM 역할에 정책 연결
resource "aws_iam_role_policy_attachment" "airflow_s3" {
  role       = aws_iam_role.airflow.name
  policy_arn = aws_iam_policy.airflow_s3.arn
}

resource "aws_iam_role_policy_attachment" "airflow_logs" {
  role       = aws_iam_role.airflow.name
  policy_arn = aws_iam_policy.airflow_logs.arn
}

# IAM 역할: Lambda용 (나중에 필요)
resource "aws_iam_role" "lambda" {
  name = "${local.resource_prefix}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = local.common_tags
}

# IAM 정책: Lambda용 S3 접근
resource "aws_iam_policy" "lambda_s3" {
  name        = "${local.resource_prefix}-lambda-s3-policy"
  description = "Lambda가 S3에 접근하기 위한 정책"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3Access"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.bronze.arn,
          "${aws_s3_bucket.bronze.arn}/*",
          aws_s3_bucket.silver.arn,
          "${aws_s3_bucket.silver.arn}/*",
          aws_s3_bucket.gold.arn,
          "${aws_s3_bucket.gold.arn}/*"
        ]
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      }
    ]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "lambda_s3" {
  role       = aws_iam_role.lambda.name
  policy_arn = aws_iam_policy.lambda_s3.arn
}

# VPC Endpoint용 정책 (선택사항)
resource "aws_iam_policy" "vpc_endpoint" {
  name        = "${local.resource_prefix}-vpc-endpoint-policy"
  description = "S3 VPC Endpoint 접근 정책"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3VPCEndpointAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Resource = [
          "${aws_s3_bucket.bronze.arn}/*",
          "${aws_s3_bucket.silver.arn}/*",
          "${aws_s3_bucket.gold.arn}/*"
        ]
      }
    ]
  })

  tags = local.common_tags
}
