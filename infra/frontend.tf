# ==============================================================================
# 프론트엔드 정적 호스팅 (서버리스) — S3 (비공개) + CloudFront (OAC)
#
# 이 파일 하나로 완결 (locals.tf / variables.tf 를 건드리지 않음 - 인프라 다른 작업과
# 머지 충돌 방지). 참조하는 건 기존 locals.tf 의 resource_prefix / common_tags /
# aws_caller_identity 뿐.
#
# - frontend/ 의 정적 자산(index.html, js/, css/)과 대시보드 데이터(mock/*.json 5개)를
#   같은 버킷 / 같은 CloudFront 배포에서 서빙 -> CORS 불필요, API Gateway 불필요.
# - mock/*.json 은 파이프라인이 4시간마다 덮어쓰므로 /mock/* 경로만 짧은 TTL(기본 120초).
#   나머지 정적 자산은 기본 TTL(1일).
# - 커스텀 도메인은 선택(frontend_domain). 미설정 시 *.cloudfront.net 기본 도메인 사용.
#
# !! terraform apply 는 여기서 하지 않는다 (state 를 가진 다른 환경에서 수행) !!
#
# 배포 후 정적 자산 업로드:
#   aws s3 sync frontend/ s3://<frontend_bucket>/ --delete --exclude 'mock/*'
# 대시보드 데이터 갱신(파이프라인 마지막 단계):
#   aws s3 cp <5개 json> s3://<frontend_bucket>/mock/
#   aws cloudfront create-invalidation --distribution-id <id> --paths '/mock/*'
# ==============================================================================

variable "frontend_domain" {
  description = "대시보드 커스텀 도메인 (예: dashboard.example.com). null이면 CloudFront 기본 도메인 사용"
  type        = string
  default     = null
}

variable "frontend_acm_certificate_arn" {
  description = "frontend_domain 사용 시 필요한 ACM 인증서 ARN (반드시 us-east-1 발급). frontend_domain 이 null 이면 무시"
  type        = string
  default     = null
}

variable "frontend_data_ttl_seconds" {
  description = "CloudFront 가 /mock/*.json 을 캐시하는 시간(초). 파이프라인이 4시간마다 갱신하므로 짧게"
  type        = number
  default     = 120
}

locals {
  frontend_bucket_name = "${local.resource_prefix}-frontend-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "frontend" {
  bucket        = local.frontend_bucket_name
  force_destroy = true # 프로젝트성: destroy 시 객체까지 정리

  tags = merge(local.common_tags, {
    Name = "${local.resource_prefix}-frontend"
    Tier = "Frontend"
  })
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# CloudFront -> S3 비공개 읽기 (OAC)
resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${local.resource_prefix}-frontend-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${local.resource_prefix} dashboard"
  default_root_object = "index.html"
  price_class         = "PriceClass_200" # 북미+유럽+아시아. 더 줄이려면 PriceClass_100

  aliases = var.frontend_domain == null ? [] : [var.frontend_domain]

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "s3-frontend"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  # 기본: 정적 자산 (index.html, js/, css/) — 1일 캐시
  default_cache_behavior {
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    min_ttl                = 0
    default_ttl            = 86400
    max_ttl                = 31536000

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }
  }

  # 대시보드 데이터: 파이프라인이 4시간마다 덮어씀 -> 짧은 TTL
  ordered_cache_behavior {
    path_pattern           = "/mock/*"
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    min_ttl                = 0
    default_ttl            = var.frontend_data_ttl_seconds
    max_ttl                = var.frontend_data_ttl_seconds

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }
  }

  # 단일 index.html 앱 — 알 수 없는 경로도 index.html 로
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 10
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = var.frontend_domain == null
    acm_certificate_arn            = var.frontend_domain == null ? null : var.frontend_acm_certificate_arn
    ssl_support_method             = var.frontend_domain == null ? null : "sni-only"
    minimum_protocol_version       = var.frontend_domain == null ? "TLSv1" : "TLSv1.2_2021"
  }

  tags = merge(local.common_tags, {
    Name = "${local.resource_prefix}-frontend"
  })
}

# 이 CloudFront 배포만 GetObject 허용
resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowCloudFrontRead"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.frontend.arn}/*"
      Condition = {
        StringEquals = {
          "AWS:SourceArn" = aws_cloudfront_distribution.frontend.arn
        }
      }
    }]
  })
}

output "frontend_bucket" {
  description = "정적 자산 + mock/*.json 을 올릴 S3 버킷"
  value       = aws_s3_bucket.frontend.id
}

output "frontend_cloudfront_domain" {
  description = "대시보드 접속 도메인 (https://<이 값>)"
  value       = aws_cloudfront_distribution.frontend.domain_name
}

output "frontend_cloudfront_distribution_id" {
  description = "캐시 무효화(aws cloudfront create-invalidation)에 사용"
  value       = aws_cloudfront_distribution.frontend.id
}
