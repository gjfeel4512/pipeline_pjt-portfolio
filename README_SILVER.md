# Pipeline_pjt Silver Layer - Airflow 기반 ETL

YouTube 데이터를 **Bronze**(원본)에서 **Silver**(정제)로 변환하는 Airflow 기반 ETL 파이프라인입니다.

## 개요

### Silver Layer 역할

Bronze Layer 문제점:
- 데이터 타입 혼재 (숫자가 문자열)
- 누락된 필드
- 시간대 혼재 (UTC만 있음)

Silver Layer 해결:
- ✅ 데이터 정제 (타입 변환)
- ✅ 파생 필드 계산 (duration_seconds, engagement_rate 등)
- ✅ 시간대 변환 (UTC → KST)
- ✅ 데이터 검증 (is_valid)

## 프로젝트 구조

```
Pipeline_pjt/
├── dags/
│   └── bronze_to_silver_dag.py
├── transforms/
│   └── silver_transform.py
├── outputs/
│   ├── bronze_merged/  (입력)
│   └── silver/         (출력)
├── reports/            (결과 리포트)
├── docker-compose.yml
├── Dockerfile.airflow
├── requirements.txt
└── README_SILVER.md
```

## 빠른 시작 (Docker)

### 1. 이미지 빌드
```bash
cd Pipeline_pjt
docker-compose build
```

### 2. Airflow DB 초기화
```bash
docker-compose up -d postgres
sleep 10
docker-compose run --rm airflow-webserver db init
docker-compose run --rm airflow-webserver users create \
  --username admin \
  --password admin \
  --firstname Admin \
  --lastname User \
  --role Admin \
  --email admin@example.com
```

### 3. 시작
```bash
docker-compose up -d
```

### 4. 접속
```
http://localhost:8080
Username: admin
Password: admin
```

## DAG 활성화 및 실행

### 1. DAG 활성화
1. Airflow UI에서 `bronze_to_silver` DAG 찾기
2. 토글 버튼 켜기 (파란색 ON)

### 2. 변수 설정 (Admin > Variables)
- `pipeline_project_root`: `/pipeline`
- `bronze_data_dir`: `outputs/bronze_merged`
- `silver_data_dir`: `outputs/silver`

### 3. 수동 실행 (테스트)
```bash
docker-compose exec airflow-webserver airflow dags test bronze_to_silver
```

또는 UI에서 "Trigger DAG" 클릭

### 4. 자동 스케줄
DAG이 활성화되면 **매일 오전 2시(KST)**에 자동 실행

## Silver Layer 변환 로직

### 1. 데이터 정제
```python
# 입력 (Bronze)
{"view_count": "3108", "like_count": "110"}

# 출력 (Silver)
{"view_count": 3108, "like_count": 110}
```

### 2. 파생 필드

#### duration_seconds (ISO 8601 → 초)
```
PT8M46S → 526초
PT1H30M45S → 5445초
```

#### video_type (길이 분류)
```
0~240초: "short"
240~1200초: "medium"
1200초+: "long"
```

#### 시간 변환 (UTC → KST)
```
입력: "2026-08-31T12:00:27Z"
출력: "2026-08-31 21:00:27+09:00"
```

#### engagement_rate
```
(like_count + comment_count) / view_count
```

### 3. 검증
```python
필수 필드: video_id, channel_id, title, published_at, view_count, category_id
is_valid = (모든 필수 필드 존재) AND (조회수 >= 0)
```

## 로그 확인

### Docker 로그
```bash
# 웹서버
docker-compose logs -f airflow-webserver

# 스케줄러
docker-compose logs -f airflow-scheduler

# 특정 DAG
docker-compose logs --tail 50 airflow-scheduler | grep bronze_to_silver
```

### 리포트 확인
```bash
cat outputs/reports/transformation_report_*.json | python3 -m json.tool
```

## 데이터 확인

### Silver 데이터 샘플
```bash
head -1 outputs/silver/gaming_2026-08.jsonl | python3 -m json.tool
```

### 통계
```bash
wc -l outputs/silver/*.jsonl
```

## 트러블슈팅

### 연결 안 됨
```bash
docker-compose ps
docker-compose logs airflow-webserver
docker-compose restart airflow-webserver
```

### DB 오류
```bash
docker-compose down -v
docker-compose up -d postgres
docker-compose run --rm airflow-webserver db init
```

### DAG 보이지 않음
```bash
python dags/bronze_to_silver_dag.py
docker-compose logs airflow-scheduler | grep bronze_to_silver
```

## 다음 단계

- Gold Layer: 최종 분석 데이터 생성
- 대시보드: Metabase/Superset 연동
- 실시간: Kafka 추가 (필요시)

---

