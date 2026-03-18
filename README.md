# NBT-Project
> Current Version: v5.1

## 1. 프로젝트 개요

Python과 Netmiko를 활용하여 Cisco IOS 네트워크 장비 설정을 자동으로 백업하고 이력을 관리하는 Web 기반 자동화 도구입니다.<br>
수동 백업으로 인한 휴먼 에러를 방지하고, 작업 이력을 체계적으로 관리하여 운영 안정성을 높이는 것을 목표로 합니다.

- 언어/프레임워크: Python 3.11, Netmiko, FastAPI, Jinja2, Celery
- 실행 환경: Docker (docker-compose 멀티 컨테이너)
- 백업 저장 위치: Ubuntu 호스트 `~/nbt-backup` (볼륨 마운트)
- Web UI: http://서버IP:8000

## 2. 주요 기능

### 2-1. 백업
- Cisco IOS 장비 설정 자동 백업 (SSH 접속, show 명령어 수집)
- 간헐적 네트워크 오류 대응 자동 재시도
- Celery + Redis 기반 비동기 백업 (브라우저 종료 후에도 지속)
- Celery Beat 자동 스케줄러 (매일 지정 시각 전체 백업)
- 단일 장비 즉시 백업 (체크박스 선택)

### 2-2. 모니터링
- Config Diff 감지: 직전 백업과 비교하여 실제 변경만 감지
- Config Diff Split View: 이전/최신 백업 좌우 분할 비교
- WebSocket 실시간 로그 스트리밍
- 백업 파일 내용 조회 및 인라인 검색
- SQLite DB 이력 관리 (WAL 모드)

### 2-3. AI 분석
- Ollama 로컬 LLM 기반 Config 분석 (외부 API 전송 없음)
- ChromaDB RAG 파이프라인: 벡터 인덱싱 후 유사 청크 검색
- 다중 장비 비교 분석: 장비별 설정 차이 분석

### 2-4. 운영
- Web UI 기반 장비 관리 (추가/수정/비활성화)
- JWT 기반 로그인 인증 (HttpOnly 쿠키)
- Slack / Email 알림 (장비 실패, Config 변경, 백업 완료)
- 자동 보존 기간 관리 (DB 90일, 파일 365일)
- YAML 기반 설정 관리 (코드 수정 없이 명령어 변경)

## 3. 프로젝트 구조
```
NBT-Project/
├── api/
│   └── routers/
│       ├── analyze.py      # POST /api/analyze, /api/analyze/index, /api/analyze/compare
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
│   ├── analyzer.py         # Ollama LLM 호출 로직 (단일/비교 분석)
│   ├── rag.py              # ChromaDB RAG 파이프라인 (청크 분할, 임베딩, 검색)
│   ├── auth.py             # JWT 인증, 비밀번호 검증, 의존성 함수
│   ├── celery_app.py       # Celery 앱 인스턴스 + beat_schedule
│   ├── tasks.py            # backup_task + cleanup_task
│   └── backup.py           # 백업 실행 메인 로직
├── utils/
│   ├── config_loader.py
│   ├── db_manager.py       # SQLite 이력 관리 (WAL 모드)
│   └── notifier.py
├── web/
│   └── templates/
│       ├── base.html       # 공통 레이아웃 (로그아웃 버튼, handle401)
│       ├── login.html      # 로그인 페이지
│       ├── index.html      # 대시보드 (Chart.js 시각화)
│       ├── backup.html     # 백업 실행 + 실시간 로그
│       ├── history.html    # 백업 이력 + 파일 뷰어 모달
│       ├── diff.html       # Config Diff split view
│       ├── devices.html    # 장비 관리 (그룹 필터 + IP 검색)
│       └── analyze.html    # AI 분석 (단일/비교 분석 탭)
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
- EVE-NG 또는 실제 운영 장비 (Cisco IOS 기준)
- Full 버전: RAM 10GB 이상 권장 (Ollama 모델 + ChromaDB 운영)
- Lite 버전 (v4.4-stable, AI 기능 제외): RAM 4GB 이상

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

# 자동 백업 스케줄 (기본: 매일 02:00 KST)
NBT_SCHEDULE_HOUR=2
NBT_SCHEDULE_MINUTE=0

# 보존 기간
NBT_DB_RETENTION_DAYS=90
NBT_FILE_RETENTION_DAYS=365

# Ollama AI 분석
NBT_OLLAMA_URL=http://nbt-ollama:11434
NBT_OLLAMA_MODEL=qwen2.5:7b

# ChromaDB RAG
NBT_CHROMA_URL=http://nbt-chroma:8000
NBT_EMBED_MODEL=nomic-embed-text
```

### 4-4. 실행
```bash
# 이미지 빌드
docker compose build

# 서비스 시작
docker compose up -d

# Ollama 모델 설치 (최초 1회)
docker compose exec nbt-ollama ollama pull qwen2.5:7b
docker compose exec nbt-ollama ollama pull nomic-embed-text

# 브라우저 접속
http://서버IP:8000
```

백업 결과는 Ubuntu 호스트 `~/nbt-backup/YYYYMMDD_HHMM/` 폴더에 저장됩니다.

## 5. 아키텍처
```
[브라우저]
    ↕ HTTP + HttpOnly 쿠키 (JWT)
[nbt-engine: FastAPI]  → 인증 검증 + 작업 등록 + AI 분석/비교 요청
    ↕                          ↕                    ↕
[redis]                [nbt-ollama: Ollama]   [nbt-chroma: ChromaDB]
  작업 큐 + pub/sub      qwen2.5:7b             벡터 인덱스 저장
    ↕                   nomic-embed-text        코사인 유사도 검색
[nbt-worker: Celery]  → 실제 SSH 백업 + cleanup 실행
    ↑
[nbt-beat: Celery Beat]  → 매일 02:00 백업 / 03:00 cleanup
    ↕
[네트워크 장비]
```

## 6. 보안 주의사항

- `.env` 파일은 절대 Git에 push하지 마세요 (.gitignore에 등록되어 있습니다)
- `config/settings.yaml`은 장비 IP가 포함되므로 Git에 push하지 마세요
- 계정 정보는 반드시 `.env` 또는 환경변수로만 관리합니다
- `ADMIN_PASSWORD`, `JWT_SECRET`은 반드시 `.env`에서 관리합니다
- `docker compose restart`는 환경변수를 재로드하지 않습니다. 환경변수 변경 시 반드시 `down → up` 순서로 재시작하세요
- Ollama와 ChromaDB는 외부 포트를 노출하지 않습니다. nbt-net 내부 네트워크에서만 접근 가능합니다
- 백업 파일(running-config 등)은 외부 API로 전송되지 않습니다. 모든 AI 분석은 로컬 Ollama에서 처리됩니다

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
  v4.3   - JWT 로그인 + UI 고도화
  v4.4   - 단일 장비 백업 + 스케줄러 + cleanup + 차트

v5.x  : AI
  v5.0   - Ollama 로컬 LLM Config 분석 API
  v5.1   - RAG 파이프라인 (ChromaDB + 임베딩) + 다중 장비 비교 분석   <- 현재 버전
  v5.2   - 자동 분석 리포트 (백업 완료 후 AI 분석 → 메일 발송)
```

## 8. 라이선스

이 프로젝트는 MIT 라이선스를 따릅니다.<br>
자세한 내용은 [LICENSE](LICENSE) 파일을 참고하세요.