# Pipeline Project: YouTube 데이터 수집 및 ETL 파이프라인

## 📋 프로젝트 개요

**프로젝트명:** Pipeline_pjt (YouTube Data Collection & ETL)  
**경로:** `C:\Pipeline_pjt`  
**상태:** 진행 중 (Bronze 완료, Silver/Gold 진행 중)

### 🎯 목표

YouTube의 트렌드 영상 데이터를 **정기적으로 수집**하고, **품질 검증**을 거쳐 **분석 가능한 형태**로 제공하는 자동화된 데이터 파이프라인 구축.

- **데이터 수집 자동화**: Lambda + Airflow 기반 정기 수집 (일 3회)
- **데이터 품질 관리**: 정제, 검증, 이상치 탐지 (Silver Layer)
- **분석 준비 완료**: 비즈니스 로직 적용, 통계 계산 (Gold Layer)
- **의사결정 지원**: BI 대시보드 및 인사이트 제공

---

## 📊 핵심 지표

| 항목 | 내용 |
|------|------|
| **대상 카테고리** | 영화·애니메이션, 자동차·차량, 게임, 인물·블로그 (4개) |
| **수집 기간** | 2025년 9월 ~ 2026년 8월 (1년) |
| **데이터 규모** | Bronze 77 MB, 822개 원본 파일 |
| **수집 방식** | YouTube Data API v3 (Search, Videos, Channels) |
| **업데이트 주기** | 일 3회 (EventBridge 트리거) |
| **팀 구성** | 데이터 엔지니어(수집), ETL 개발자(파이프라인), 프론트엔드(대시보드) |

---

## 🏗️ 데이터 흐름 (ELT 아키텍처)

```
┌─────────────────┐
│ YouTube API     │
│ (Search, Video, │
│  Channels)      │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│ EXTRACT (추출)                          │
├─────────────────────────────────────────┤
│ • Lambda (일 3회) - mostPopular         │
│ • Collector (1회성) - 1년치 백필        │
│ API 키 8개 자동 순환, 할당량 관리       │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│ LOAD (적재) - Bronze Layer              │
├─────────────────────────────────────────┤
│ S3 경로:                                 │
│ ├─ bronze_collect/ (API 원본 JSON)     │
│ └─ bronze_merged/ (카테고리별 JSONL)    │
│                                         │
│ 특징: 검증/가공 전 원본 그대로 저장     │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│ TRANSFORM (변환) - Silver Layer         │
├─────────────────────────────────────────┤
│ • 데이터 정제 (null, 타입 변환)         │
│ • 파생 필드 계산 (duration_seconds,    │
│   video_type, published_kst 등)        │
│ • 이상치 감지 및 필터링                 │
│ • 검증 플래그 추가 (is_valid)           │
│                                         │
│ 저장소: Postgres (raw_youtube 스키마)   │
│ 파일: S3 Parquet (파티션: 수집일자)    │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│ ANALYZE (분석) - Gold Layer             │
├─────────────────────────────────────────┤
│ 비즈니스 로직 적용:                      │
│ • gold_category_benchmark (카테고리별)  │
│ • gold_upload_strategy (업로드 전략)    │
│ • gold_video_rank_trend (순위 변동)     │
│ • gold_channel_insights (채널 인사이트) │
│                                         │
│ 도구: Postgres + Glue + Athena          │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│ 시각화 & 분석                            │
├─────────────────────────────────────────┤
│ • 대시보드 (프론트엔드)                  │
│ • SQL 조회 (Athena)                     │
│ • BI 도구 연동 (선택)                    │
└─────────────────────────────────────────┘
```

---

## 🛠️ 기술 스택

### 데이터 수집 계층 (Extract)

| 구성요소 | 역할 | 특징 |
|---------|------|------|
| **Lambda** | 정기 수집 (일 3회) | EventBridge 트리거, 상시 운영 |
| **Python Collector** | 초기 1년치 백필 | 1회성, 할당량 최적화 |
| **YouTube Data API v3** | 데이터 소스 | 일 10,000 할당량 제한 |
| **API 키 순환** | 할당량 관리 | 8개 키 자동 전환 |

### 데이터 저장소 (Load / Transform)

| 저장소 | 계층 | 용도 | 파일 크기 |
|--------|------|------|----------|
| **S3** | Bronze | API 원본 JSON 보존 | 77 MB |
| **S3 + Parquet** | Silver | 정제 데이터 저장 | ~15 MB (예상) |
| **Postgres** | Raw/Gold | 변환 및 분석 데이터 | 인메모리 |
| **Glue + Athena** | Gold | SQL 조회 및 분석 | 메타데이터 기반 |

### 오케스트레이션 (Workflow)

| 도구 | 역할 | 구성 |
|------|------|------|
| **Airflow** | DAG 스케줄링 | LocalExecutor, 로컬 Docker |
| **EventBridge** | Lambda 트리거 | 일 3회 정기 실행 |
| **Bash Operator** | 작업 실행 | 수집 → 정제 → 적재 → 분석 |

### 프론트엔드 (Visualization)

| 파일 | 역할 |
|------|------|
| **index.html** | 4탭 대시보드 (홈/추천채널/업로드가이드/카테고리트렌드) |
| **app.js** | 탭 전환, 필터 로직 |
| **styles.css** | 카드, 배지, 히트맵, 차트 스타일 |
| **mock-data.js** | 하드코딩 데이터 (API 연동 준비) |

---

## 📁 프로젝트 구조

```
Pipeline_pjt/
├── youtube_api_collector.py      # 1년치 백필 수집기
├── outputs/
│   ├── bronze_collect/           # API 원본 JSON (822개 파일)
│   ├── bronze_merged/            # 카테고리별 병합 (JSONL)
│   └── api_checkpoint.json       # 진행 상황 체크포인트
│
├── dags/
│   ├── daily_lambda_to_silver_dag.py    # Lambda → Silver 변환
│   └── silver_to_gold_dag.py            # Postgres 적재 → Gold 계산
│
├── transforms/
│   ├── validate_silver.py        # 데이터 검증 & Reject
│   ├── load_silver_to_postgres.py# Silver → Postgres
│   └── export_gold_to_s3.py      # Gold → S3 export
│
├── sql/
│   ├── youtube_pipeline_schema_postgresql.sql
│   └── compute_gold.sql          # Gold 테이블 계산
│
├── infra/
│   ├── terraform/                # AWS 리소스 IaC
│   └── docker-compose.yml        # 로컬 Postgres, Airflow
│
└── frontend/
    ├── index.html, styles.css, app.js
    ├── mock-data.js              # 하드코딩 데이터
    └── README.md                 # 대시보드 가이드
```

---

## 🔄 데이터 처리 파이프라인

### 1️⃣ Extract (YouTube API 수집)

**수집 전략:**
- **Lambda 수집** (`daily_mostpopular_collector`): 일 3회, 트렌드 영상 50개 (카테고리별)
- **로컬 수집** (`youtube_api_collector.py`): 1회성, 1년치 백필 (search API)

**특징:**
- API 할당량: 일 10,000 단위 (1회 요청 = 1~100 단위)
- 키 순환: 8개 API 키 자동 전환
- 체크포인트: `api_checkpoint.json`으로 진행 상황 추적

**출력:**
- Raw JSON (API 응답 그대로): `bronze_collect/` (822개 파일, 77 MB)
- JSONL (라인 단위): `bronze_merged/` (카테고리별)

### 2️⃣ Load & Transform (Silver Layer - 정제)

**Airflow DAG:** `daily_lambda_to_silver_dag`

| 단계 | 작업 | 입력 | 출력 |
|------|------|------|------|
| 1 | Lambda 실행 | YouTube API | S3 JSON |
| 2 | 검증 | S3 JSON | Valid/Reject 분리 |
| 3 | 변환 | Valid 레코드 | Parquet (파티션) |
| 4 | 메타데이터 | Parquet | Glue 카탈로그 등록 |

**변환 로직:**
```python
# 파생 필드 계산
- duration_seconds: ISO 8601 → 초 단위
- video_type: duration 기반 분류 (short/medium/long)
- published_kst: UTC → KST 변환
- published_year_month: 년월 그룹화
- trending_rank: Lambda 순위 캡처

# 검증
- is_valid: 필수 필드 존재 여부
- 이상치: 음수 조회수, null 필드 등
```

**저장소:**
- Postgres: `raw_youtube.silver_youtube` (행 단위)
- S3 Parquet: `s3://bucket/silver/youtube/year=YYYY/month=MM/day=DD/`

### 3️⃣ Analyze (Gold Layer - 분석)

**Airflow DAG:** `silver_to_gold_dag`

| 금고 테이블 | 내용 | 사용 사례 |
|------------|------|----------|
| **gold_category_benchmark** | 카테고리별 주차 통계 (조회수, 좋아요, 댓글) | 카테고리 트렌드 분석 |
| **gold_upload_strategy** | 요일×시간대별 최적 업로드 시간 | 콘텐츠 업로드 가이드 |
| **gold_video_rank_trend** | 영상 순위 변동, 조회수 증감 추이 | 순위 변동 리스트 |
| **gold_channel_insights** | 채널별 성장률, 영상 수, 구독자 추이 | 채널 추천 (향후) |

**계산 방식:**
```sql
-- 예: gold_video_rank_trend
SELECT 
  video_id,
  MIN(trending_rank) OVER (...) as first_rank,
  MAX(trending_rank) OVER (...) as highest_rank,
  ROW_NUMBER() OVER (...) as latest_rank,
  ...
FROM raw_youtube.silver_youtube
```

---

## 📅 구현 일정 및 현황

### Phase 1: 기반 구축 ✅ 완료

| 작업 | 상태 | 내용 |
|------|------|------|
| API 키 및 수집기 구현 | ✅ 완료 | `youtube_api_collector.py`, Lambda function |
| Bronze Layer 적재 | ✅ 완료 | 77 MB 원본 데이터 수집 |
| 로컬 인프라 (Docker) | ✅ 완료 | Postgres, Airflow LocalExecutor |
| Airflow DAG 기본 구조 | ✅ 완료 | `daily_lambda_to_silver_dag` 작성 |

### Phase 2: Silver Layer (데이터 정제) 🔄 진행 중

| 작업 | 상태 | 예상 완료 |
|------|------|----------|
| 검증 로직 구현 | ✅ 완료 | Reject JSON 분리 |
| 파생 필드 계산 | ✅ 완료 | `duration_seconds`, `video_type` 등 |
| Postgres 스키마 적용 | ⏳ 대기 | 스키마 DDL 실행 필요 |
| Glue 메타데이터 등록 | ⏳ 진행 중 | 카탈로그 동기화 |

### Phase 3: Gold Layer (분석 준비) 🔄 진행 중

| 작업 | 상태 | 내용 |
|------|------|------|
| 계산 쿼리 작성 | ✅ 완료 | `compute_gold.sql` 4개 테이블 정의 |
| DAG 트리거 | ⏳ 대기 | `silver_to_gold_dag` 수행 확인 필요 |
| Athena View 생성 | ⏳ 대기 | SQL 조회 최적화 |

### Phase 4: 프론트엔드 (대시보드) 🔄 진행 중

| 컴포넌트 | 상태 | 내용 |
|----------|------|------|
| 홈 탭 | ✅ v2 완료 | KPI + 순위 변동 리스트 |
| 추천 채널 탭 | ⏳ 진행 | 채널 선별 로직 (백엔드 미구현) |
| 업로드 가이드 탭 | ✅ v2 완료 | 요일×시간대 히트맵 |
| 카테고리 트렌드 탭 | ✅ v2 완료 | 막대 차트 + 표 |
| API 연동 | ⏳ 예정 | 하드코딩 데이터 → 실 API 호출 |

---

## ⚠️ 미해결 사항 (우선순위 순)

| 우선순위 | 항목 | 상태 | 대응 |
|---------|------|------|------|
| 🟡 **중간** | Postgres 스키마 | 대기 중 | DDL 실행 필요: `docker exec pipeline_postgres psql ...` |
| 🟡 **중간** | Gold DAG 실행 | 대기 중 | `airflow dags trigger silver_to_gold` 확인 필요 |
| 🟢 **낮음** | 스냅샷 세분화 | 미결정 | 현재 일 단위 (하루 3회 → 일일 1개 스냅샷), 시간 단위로 변경할지 검토 |
| 🟢 **낮음** | Lambda 에러 처리 | 미개선 | 카테고리별 try/except 추가하면 좋음 (백로그) |

---

## 💾 데이터 크기 및 비용 추정

### 저장소 비용 (월 기준)

| 서비스 | 사용량 | 예상 월비용 | 비고 |
|--------|--------|-----------|------|
| S3 Standard | 100 MB (Bronze + Silver) | ~$0.02 | 스토리지 기본 요금 |
| Glue Data Catalog | 메타데이터 무료 구간 | $0.00 | 테이블/파티션 미포함 |
| Athena | 1 TB 스캔 (예상) | ~$5.00 | $5/TB 기준 |
| Postgres (로컬) | 10 GB (EBS) | $0.00 | Docker, 클라우드 비용 없음 |
| **합계** | | **~$5.02/월** | |

### 운영 비용 (추가 필요 시)

- **EC2 상시운영:** t3.small (2 GiB) = 약 $15/월
- **Airflow MWAA:** Managed Workflow = 약 $50/월 (선택)

---

## 🎯 성공 기준

| 검증 항목 | 완료 증거 | 상태 |
|----------|----------|------|
| Airflow 정기 실행 | DAG 성공 화면 & Task 로그 | ⏳ 대기 |
| Bronze / Silver / Reject | S3 경로별 파일 생성 확인 | ⏳ 진행 중 |
| Gold 테이블 계산 | Postgres 테이블 row count | ⏳ 대기 |
| 대시보드 데이터 연동 | 프론트엔드 실시간 데이터 표시 | ⏳ 예정 |
| Athena 조회 | SQL 결과 화면 캡처 | ⏳ 예정 |

---

## 🔗 연결된 리소스

### 문서
- [진행 상태 리포트](./pipeline_project_status_2026-09-02.md)
- [데이터 분석 보고서](./pipeline_project_analysis.md)

### 저장소
- **GitHub:** `https://github.com/jaeyan42/Pipeline_pjt`
- **로컬:** `C:\Pipeline_pjt`

---
