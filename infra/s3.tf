# S3 버킷: Bronze (원본 데이터)
resource "aws_s3_bucket" "bronze" {
  bucket        = local.s3_bucket_names.bronze
  force_destroy = false

  tags = merge(
    local.common_tags,
    {
      Name = "${local.resource_prefix}-bronze"
      Tier = "Bronze"
    }
  )
}

resource "aws_s3_bucket_versioning" "bronze" {
  bucket = aws_s3_bucket.bronze.id

  versioning_configuration {
    status = var.s3_versioning_enabled ? "Enabled" : "Suspended"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "bronze" {
  bucket = aws_s3_bucket.bronze.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# S3 버킷: Silver (정제된 데이터)
resource "aws_s3_bucket" "silver" {
  bucket        = local.s3_bucket_names.silver
  force_destroy = false

  tags = merge(
    local.common_tags,
    {
      Name = "${local.resource_prefix}-silver"
      Tier = "Silver"
    }
  )
}

resource "aws_s3_bucket_versioning" "silver" {
  bucket = aws_s3_bucket.silver.id

  versioning_configuration {
    status = var.s3_versioning_enabled ? "Enabled" : "Suspended"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "silver" {
  bucket = aws_s3_bucket.silver.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# S3 버킷: Gold (최종 분석 데이터)
resource "aws_s3_bucket" "gold" {
  bucket        = local.s3_bucket_names.gold
  force_destroy = false

  tags = merge(
    local.common_tags,
    {
      Name = "${local.resource_prefix}-gold"
      Tier = "Gold"
    }
  )
}

resource "aws_s3_bucket_versioning" "gold" {
  bucket = aws_s3_bucket.gold.id

  versioning_configuration {
    status = var.s3_versioning_enabled ? "Enabled" : "Suspended"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "gold" {
  bucket = aws_s3_bucket.gold.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# S3 Bucket Lifecycle Policy (Bronze) - 자동 아카이빙
resource "aws_s3_bucket_lifecycle_configuration" "bronze" {
  bucket = aws_s3_bucket.bronze.id

  rule {
    id     = "delete-old-bronze"
    status = "Enabled"
    filter {}

    # 30일 후 INTELLIGENT_TIERING으로 전환
    transition {
      days          = var.s3_lifecycle_days
      storage_class = "INTELLIGENT_TIERING"
    }

    expiration {
      days = 90
    }
  }
}

# S3 Bucket Lifecycle Policy (Silver)
resource "aws_s3_bucket_lifecycle_configuration" "silver" {
  bucket = aws_s3_bucket.silver.id

  rule {
    id     = "delete-old-silver"
    status = "Enabled"
    filter {}

    # 60일 후 INTELLIGENT_TIERING으로 전환
    transition {
      days          = 60
      storage_class = "INTELLIGENT_TIERING"
    }

    expiration {
      days = 180
    }
  }
}

# S3 Bucket Lifecycle Policy (Gold)
resource "aws_s3_bucket_lifecycle_configuration" "gold" {
  bucket = aws_s3_bucket.gold.id

  rule {
    id     = "delete-old-gold"
    status = "Enabled"
    filter {}

    # 180일 후 INTELLIGENT_TIERING으로 전환
    transition {
      days          = 180
      storage_class = "INTELLIGENT_TIERING"
    }

    expiration {
      days = 365
    }
  }
}

# S3 Public Access Block (보안)
resource "aws_s3_bucket_public_access_block" "bronze" {
  bucket = aws_s3_bucket.bronze.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "silver" {
  bucket = aws_s3_bucket.silver.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "gold" {
  bucket = aws_s3_bucket.gold.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
