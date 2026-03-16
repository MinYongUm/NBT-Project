# NBT-Project
> Current Version: v4.2

## 1. 프로젝트 개요

Python과 Netmiko를 활용하여 Cisco IOS, IOS-XE, NX-OS 등 다종의 네트워크 장비 설정을 자동으로 백업하고 로그를 기록하는 Web 기반 자동화 도구입니다.
수동 백업으로 인한 휴먼 에러를 방지하고, 작업 이력을 체계적으로 관리하여 운영 안정성을 높이는 것을 목표로 합니다.

- 언어/프레임워크: Python 3.11, Netmiko, FastAPI, Jinja2, Celery
- 실행 환경: Docker (docker-compose)
- 백업 저장 위치: Ubuntu 호스트 `~/nbt-backup` (볼륨 마운트)
- Web UI: http://서버IP:8000

## 2. 주요 기능

- 다중 벤더/OS 동시 지원 (Cisco IOS, IOS-XE, NX-OS)
- 간헐적 네트워크 오류에 대응하는 자동 재시도 로직
- 실행 시간 기반 동적 폴더 생성 및 결과 파일 저장
- 성공/실패 이력을 기록하는 파일 로깅 시스템
- YAML 기반 설정 관리 (코드 수정 없이 장비/명령어 변경 가능)
- 계정 정보 환경변수 분리 (.env 기반 보안 관리)
- ThreadPoolExecutor 기반 병렬 백업 (max_workers 설정으로 제어)
- Config Diff 감지: 직전 백업과 비교하여 실제 설정 변경만 감지 (노이즈 필터링 적용)
- SQLite DB 이력 관리: 백업 실행 결과 및 Config Diff 결과 누적 저장
- FastAPI 기반 Web UI: 백업 실행, 이력 조회, Config Diff 조회
- Celery + Redis 기반 비동기 작업 큐 (백업 실행/Web 서버 분리)
- WebSocket 기반 실시간 백업 로그 스트리밍 (재연결 로직 포함)
- 장비 관리 Web UI: 장비 추가/수정/비활성화 (DB 기반 관리)
- Slack / Email 알림 (장비 실패, Config 변경, 백업 완료 요약)

## 3. 프로젝트 구조
```
NBT-Project/
├── api/
│   ├── __init__.py
│   └── routers/
│       ├── __init__.py
│       ├── backup.py              # POST /api/backup, WS /api/backup/ws/{run_id}
│       ├── devices.py             # GET/POST/PUT/DELETE /api/devices (v4.1)
│       ├── diff.py                # GET /api/diff
│       ├── history.py             # GET /api/history, GET /api/history/{run_id}
│       └── pages.py               # HTML 페이지 라우터
├── config/
│   ├── commands.yaml              # 장비별 백업 명령어
│   └── settings.yaml.example     # 장비 목록 + 백업 설정 + Diff 설정 + 알림 설정 템플릿
├── core/
│   ├── celery_app.py              # Celery 앱 인스턴스 정의 (v4.2)
│   ├── tasks.py                   # 백업 Celery task, Redis pub/sub (v4.2)
│   └── backup.py                  # 백업 실행 메인 로직
├── utils/
│   ├── config_loader.py           # YAML 파서 + 환경변수 수신
│   ├── db_manager.py              # SQLite 백업 이력, Config Diff, 장비 관리
│   ├── folder_create.py           # 백업/로그 폴더 생성
│   └── notifier.py                # Slack Webhook + Email(SMTP) 알림 모듈
├── web/
│   ├── templates/
│   │   ├── base.html              # 공통 레이아웃 (Pretendard + Geist Mono, 다크 테마)
│   │   ├── index.html             # 대시보드 (Stat 카드 + 이력 테이블)
│   │   ├── backup.html            # 백업 실행 + WebSocket 실시간 로그 (v4.2)
│   │   ├── history.html           # 백업 이력 테이블 (상세 보기 포함)
│   │   ├── diff.html              # Config Diff 이력 (컬러 렌더링)
│   │   └── devices.html           # 장비 관리 UI (v4.1)
│   └── static/                    # 정적 파일
├── app.py                         # FastAPI Web 서버 진입점
├── main.py                        # Typer CLI 진입점 (v3.1, 보조 사용)
├── .env.example                   # 환경변수 템플릿
├── Dockerfile
├── docker-compose.yml             # nbt-engine, nbt-worker, redis (v4.2)
└── requirements.txt
```

## 4. 설치 및 실행

### 4-1. 요구사항

- Docker, docker-compose
- EVE-NG 또는 실제 운영 장비 (Cisco 기준)

### 4-2. 초기 설정
```bash
# 1. 저장소 클론
git clone https://github.com/MinYongUm/NBT-Project.git
cd NBT-Project

# 2. 환경변수 파일 생성
cp .env.example .env
chmod 600 .env
# .env에 실제 계정 정보 입력

# 3. 설정 파일 생성
cp config/settings.yaml.example config/settings.yaml
# config/settings.yaml에 실제 장비 IP 입력
```

### 4-3. .env 설정
```bash
NBT_USERNAME=your_username
NBT_PASSWORD=your_password
NBT_BACKUP_ROOT=/data/backup
TZ=Asia/Seoul
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
```

### 4-4. config/settings.yaml 설정
```yaml
devices:
  mgmt:
    device_type: cisco_ios
    hosts:
      - 10.x.x.1

backup:
  max_retries: 5
  retry_delay: 10
  session_timeout: 30
  max_workers: 4

diff:
  noise_patterns:
    - "uptime is"
    - "Last reload"
```

### 4-5. 실행
```bash
# 이미지 빌드
docker compose build

# 전체 컨테이너 실행 (nbt-engine, nbt-worker, redis)
docker compose up -d
```

브라우저에서 `http://서버IP:8000` 접속

### 4-6. CLI 사용법 (보조)
```bash
# 전체 백업
docker compose run --rm nbt-engine python main.py backup

# 특정 그룹만 백업
docker compose run --rm nbt-engine python main.py backup --group mgmt

# 설정 검증만 (실제 접속 없음)
docker compose run --rm nbt-engine python main.py backup --dry-run

# 최근 백업 이력 조회
docker compose run --rm nbt-engine python main.py history

# 최근 Config Diff 조회
docker compose run --rm nbt-engine python main.py diff
```

## 5. Web API

| 메서드 | 엔드포인트 | 설명 |
|---|---|---|
| GET | / | 대시보드 |
| GET | /backup | 백업 실행 페이지 |
| GET | /history | 백업 이력 페이지 |
| GET | /diff | Config Diff 페이지 |
| GET | /devices | 장비 관리 페이지 |
| POST | /api/backup | 백업 실행 시작 (Celery task 등록) |
| WS | /api/backup/ws/{run_id} | WebSocket 실시간 로그 스트림 |
| GET | /api/history | 최근 백업 이력 조회 |
| GET | /api/history/{run_id} | 특정 run 장비별 결과 조회 |
| GET | /api/diff | 최근 Config Diff 이력 조회 |
| GET | /api/devices | 활성 장비 목록 조회 |
| POST | /api/devices | 장비 추가 |
| PUT | /api/devices/{id} | 장비 수정 |
| DELETE | /api/devices/{id} | 장비 비활성화 |
| GET | /docs | Swagger API 문서 |

## 6. 보안 주의사항

- `.env` 파일은 절대 Git에 push하지 마세요 (.gitignore에 등록되어 있습니다)
- `config/settings.yaml`은 장비 IP가 포함되므로 Git에 push하지 마세요
- 계정 정보는 반드시 `.env` 또는 환경변수로만 관리합니다
- `config/settings.yaml.example`, `.env.example`은 실제 값 없는 템플릿만 Git에 올립니다

## 7. 개발 로드맵
```
v0.x  : Paramiko
  v0.1  - Paramiko 기반 단일 장비 백업

v1.x  : Netmiko
  v1.0  - Netmiko 마이그레이션, 다중 장비 그룹 지원
  v1.1  - logging 추가, 재시도 5회
  v1.2  - session_timeout 30초

v2.x  : Docker + 자동화
  v2.0  - Docker 환경 마이그레이션
  v2.1  - YAML 설정 전환, 보안 적용
  v2.2  - SQLite DB 연동, 백업 이력 저장
  v2.3  - ThreadPoolExecutor 병렬 실행

v3.x  : 고도화
  v3.0  - Config Diff 감지, 노이즈 필터링
  v3.1  - Typer CLI, 알림(Slack/Email)

v4.x  : Web 전환
  v4.0  - FastAPI 백엔드 + Jinja2 Web UI
  v4.1  - 장비 관리 UI (settings.yaml → DB/웹 편집)
  v4.2  - Celery + Redis, WebSocket

v5.x  : AI (Web 완성 후 착수)
  v5.0  - AI 장애 분석 (LLM 직접 호출, RAG 없음)
  v5.1  - RAG 파이프라인 (ChromaDB + RFC/자작 문서)
  v5.2  - MCP 서버 (Claude Desktop 연동)
```