# NBT-Project
> Current Version: v4.3

## 1. 프로젝트 개요

Python과 Netmiko를 활용하여 Cisco IOS, IOS-XE, NX-OS 등 다종의 네트워크 장비 설정을 자동으로 백업하고 로그를 기록하는 Web 기반 자동화 도구입니다.
수동 백업으로 인한 휴먼 에러를 방지하고, 작업 이력을 체계적으로 관리하여 운영 안정성을 높이는 것을 목표로 합니다.

- 언어/프레임워크: Python 3.11, Netmiko, FastAPI, Jinja2, Celery
- 실행 환경: Docker (docker-compose 멀티 컨테이너)
- 백업 저장 위치: Ubuntu 호스트 `~/nbt-backup` (볼륨 마운트)
- Web UI: http://서버IP:8000

## 2. 주요 기능

- 다중 벤더/OS 동시 지원 (Cisco IOS, IOS-XE, NX-OS)
- 간헐적 네트워크 오류에 대응하는 자동 재시도 로직
- 실행 시간 기반 동적 폴더 생성 및 결과 파일 저장
- YAML 기반 설정 관리 (코드 수정 없이 명령어 변경 가능)
- 계정 정보 환경변수 분리 (.env 기반 보안 관리)
- Celery + Redis 기반 비동기 백업 (브라우저 종료 후에도 백업 지속)
- WebSocket 실시간 로그 스트리밍
- 중복 실행 방지 (Redis 플래그 + TTL 자동 만료)
- Config Diff 감지: 직전 백업과 비교하여 실제 설정 변경만 감지 (노이즈 필터링 적용)
- Config Diff Split View: 이전/최신 백업 좌우 분할 비교
- SQLite DB 이력 관리: WAL 모드 적용으로 백업 중 조회 동시 지원
- Web UI 기반 장비 관리: 추가/수정/비활성화, 그룹 필터 및 IP 검색
- 백업 파일 내용 조회: Web UI에서 직접 config 열람 및 인라인 검색
- JWT 기반 로그인 인증 (HttpOnly 쿠키, 단일 계정)
- Slack / Email 알림 (장비 실패, Config 변경, 백업 완료 요약)

## 3. 프로젝트 구조

```
NBT-Project/
├── api/
│   └── routers/
│       ├── auth.py         # GET /login, POST /api/auth/login, POST /api/auth/logout
│       ├── backup.py       # POST /api/backup, GET /api/backup/file, WS /api/backup/ws/{run_id}
│       ├── devices.py      # GET/POST/PUT/DELETE /api/devices
│       ├── diff.py         # GET /api/diff
│       ├── history.py      # GET /api/history
│       └── pages.py        # HTML 페이지 라우터
├── config/
│   ├── commands.yaml
│   └── settings.yaml.example
├── core/
│   ├── auth.py             # JWT 인증, 비밀번호 검증, 의존성 함수
│   ├── celery_app.py       # Celery 앱 인스턴스
│   ├── tasks.py            # 백업 Celery task
│   └── backup.py           # 백업 실행 메인 로직
├── utils/
│   ├── config_loader.py
│   ├── db_manager.py       # SQLite 이력 관리 (WAL 모드)
│   └── notifier.py
├── web/
│   └── templates/
│       ├── base.html       # 공통 레이아웃 (로그아웃 버튼, handle401)
│       ├── login.html      # 로그인 페이지
│       ├── index.html      # 대시보드
│       ├── backup.html     # 백업 실행 + 실시간 로그
│       ├── history.html    # 백업 이력 + 파일 뷰어 모달
│       ├── diff.html       # Config Diff split view
│       └── devices.html    # 장비 관리 (그룹 필터 + IP 검색)
├── app.py
├── main.py
├── .env.example
├── Dockerfile
├── docker-compose.yml
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
# .env에 실제 계정 정보 및 인증 설정 입력

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

# 로그인 인증 (필수)
ADMIN_PASSWORD=your_admin_password
JWT_SECRET=your_random_secret_key
JWT_EXPIRE_HOURS=8
```

### 4-4. 실행

```bash
# 이미지 빌드
docker compose build

# 서비스 시작
docker compose up -d

# 브라우저 접속
http://서버IP:8000
```

백업 결과는 Ubuntu 호스트 `~/nbt-backup/YYYYMMDD_HHMM/` 폴더에 저장됩니다.

## 5. 아키텍처

```
[브라우저]
    ↕ HTTP + HttpOnly 쿠키 (JWT)
[nbt-engine: FastAPI]  → 인증 검증 + 작업 등록 + 결과 전달
    ↕
[redis]  → 작업 큐 + pub/sub 로그 채널
    ↕
[nbt-worker: Celery]  → 실제 SSH 백업 실행
    ↕
[네트워크 장비]
```

## 6. 보안 주의사항

- `.env` 파일은 절대 Git에 push하지 마세요 (.gitignore에 등록되어 있습니다)
- `config/settings.yaml`은 장비 IP가 포함되므로 Git에 push하지 마세요
- 계정 정보는 반드시 `.env` 또는 환경변수로만 관리합니다
- `ADMIN_PASSWORD`, `JWT_SECRET`은 반드시 `.env`에서 관리합니다
- `docker compose restart`는 환경변수를 재로드하지 않습니다. 환경변수 변경 시 반드시 `down → up` 순서로 재시작하세요

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
  v4.0   - FastAPI 백엔드 + Jinja2 Web UI
  v4.1   - 장비 관리 UI (settings.yaml → DB/웹 편집)
  v4.2   - Celery + Redis + WebSocket
  v4.2.1 - SQLite WAL 모드, Redis TTL 방어적 설계
  v4.3   - JWT 로그인 + UI 고도화                         <- 현재 버전

v5.x  : AI
  v5.0  - AI 장애 분석 (Ollama 로컬 LLM)
  v5.1  - RAG 파이프라인 (ChromaDB)
  v5.2  - MCP 서버 (Claude Desktop 연동)
```