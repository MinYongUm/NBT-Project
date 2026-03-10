# NBT-Project
> Current Version: v2.1
## 1. 프로젝트 개요
Python과 Netmiko를 활용하여 Cisco IOS, IOS-XE, NX-OS 등 다종의 네트워크 장비 설정을 자동으로 백업하고 로그를 기록하는 CLI 기반 자동화 도구입니다.
수동 백업으로 인한 휴먼 에러를 방지하고, 작업 이력을 체계적으로 관리하여 운영 안정성을 높이는 것을 목표로 합니다.

- 언어/프레임워크: Python 3.11, Netmiko
- 실행 환경: Docker (docker-compose)
- 백업 저장 위치: Ubuntu 호스트 `~/nbt-backup` (볼륨 마운트)

## 2. 주요 기능
- 다중 벤더/OS 동시 지원 (Cisco IOS, IOS-XE, NX-OS)
- 간헐적 네트워크 오류에 대응하는 자동 재시도 로직
- 실행 시간 기반 동적 폴더 생성 및 결과 파일 저장
- 성공/실패 이력을 기록하는 파일 로깅 시스템
- YAML 기반 설정 관리 (코드 수정 없이 장비/명령어 변경 가능)
- 계정 정보 환경변수 분리 (.env 기반 보안 관리)

## 3. 프로젝트 구조
```
NBT-Project/
├── config/
│   ├── commands.yaml           # 장비별 백업 명령어
│   └── settings.yaml.example   # 장비 목록 + 백업 설정 템플릿
├── core/
│   └── backup.py               # 백업 실행 메인 로직
├── utils/
│   ├── config_loader.py        # YAML 파서 + 환경변수 수신
│   └── folder_create.py        # 백업/로그 폴더 생성
├── .env.example                # 환경변수 템플릿
├── Dockerfile
├── docker-compose.yml
├── main.py
└── requirements.txt
```
## 4. 설치 및 실행

### 4-1. 요구사항
- Docker, docker-compose
- EVE-NG 또는 실제 운영 장비(Cisco 기준)

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

### 4-3. env 설정

```bash
NBT_USERNAME=your_username
NBT_PASSWORD=your_password
NBT_BACKUP_ROOT=/data/backup
TZ=Asia/Seoul
```

### 4-4. config/settings.yaml 설정

```yaml
devices:
  mgmt:
    device_type: cisco_ios
    hosts:
      - 10.x.x.1

  nexus:
    device_type: cisco_nxos
    hosts:
      - 10.x.x.10

  aci:
    device_type: cisco_nxos
    hosts:
      - 10.x.x.20

backup:
  max_retries: 5
  retry_delay: 10
  session_timeout: 30
```

### 4-5. 실행

```bash
# 이미지 빌드
docker compose build

# 백업 실행
docker compose run --rm nbt-engine python main.py
```

백업 결과는 Ubuntu 호스트 `~/nbt-backup/YYYYMMDD_HHMM/` 폴더에 저장됩니다.

## 5. 보안 주의사항

- `.env` 파일은 절대 Git에 push하지 마세요 (.gitignore에 등록되어 있습니다)
- `config/settings.yaml`은 장비 IP가 포함되므로 Git에 push하지 마세요
- 계정 정보는 반드시 `.env` 또는 환경변수로만 관리합니다
- `config/settings.yaml.example`, `.env.example`은 실제 값 없는 템플릿만 Git에 올립니다

## 6. 개발 로드맵

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
  v2.2  - 데이터 구조화 (AI 분석 대비)
  v2.3  - 병렬 실행 (concurrent.futures)

v3.x  : 고도화
  v3.0  - Config Diff + DB 저장 (difflib + SQLite)
  v3.1  - CLI + 알림 (Typer + Slack/Email)

v4.x  : AI
  v4.0  - AI 장애 분석, RAG + MCP 서버
```