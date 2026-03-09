"""
NBT (Network Backup Tools) - Configuration Loader
설정 파일(YAML) 및 환경변수를 로드하고 검증합니다.

우선순위:
    계정 정보: 환경변수 > .env 파일  (settings.yaml에서는 읽지 않음)
    장비/백업 설정: settings.yaml
    명령어: commands.yaml
"""

import os
import logging
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# =================================================================
# 경로 상수
# =================================================================
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"

SETTINGS_PATH = CONFIG_DIR / "settings.yaml"
COMMANDS_PATH = CONFIG_DIR / "commands.yaml"
ENV_PATH = PROJECT_ROOT / ".env"


# =================================================================
# 내부 헬퍼
# =================================================================
def _load_yaml(path: Path) -> dict[str, Any]:
    """YAML 파일을 로드합니다."""
    if not path.exists():
        raise FileNotFoundError(
            f"설정 파일을 찾을 수 없습니다: {path}\n"
            f"  -> {path.name}.example 파일을 복사하여 생성하세요."
        )
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _validate_settings(settings: dict[str, Any]) -> None:
    """settings.yaml 필수 키를 검증합니다."""
    required_sections = ["devices", "backup"]
    for section in required_sections:
        if section not in settings:
            raise ValueError(f"settings.yaml에 필수 섹션이 없습니다: [{section}]")

    required_device_groups = ["mgmt", "nexus", "aci"]
    for group in required_device_groups:
        if group not in settings["devices"]:
            raise ValueError(f"settings.yaml devices 섹션에 [{group}] 그룹이 없습니다.")
        group_cfg = settings["devices"][group]
        if "device_type" not in group_cfg:
            raise ValueError(f"devices.{group}에 'device_type' 키가 없습니다.")
        if "hosts" not in group_cfg or not group_cfg["hosts"]:
            raise ValueError(f"devices.{group}에 'hosts' 목록이 없습니다.")

    required_backup_keys = ["max_retries", "retry_delay", "session_timeout"]
    for key in required_backup_keys:
        if key not in settings["backup"]:
            raise ValueError(f"settings.yaml backup 섹션에 [{key}] 키가 없습니다.")


def _validate_commands(commands: dict[str, Any]) -> None:
    """commands.yaml 필수 키를 검증합니다."""
    required_groups = ["mgmt", "nexus", "aci"]
    for group in required_groups:
        if group not in commands:
            raise ValueError(f"commands.yaml에 [{group}] 그룹이 없습니다.")
        if not isinstance(commands[group], list) or not commands[group]:
            raise ValueError(f"commands.yaml [{group}]의 명령어 목록이 비어 있습니다.")


def _load_credentials() -> tuple[str, str]:
    """
    계정 정보를 환경변수 우선으로 로드합니다.

    우선순위:
        1. 시스템 환경변수 (Docker 컨테이너, CI/CD 등에서 주입)
        2. .env 파일 (로컬 개발 환경)

    Returns:
        (username, password) 튜플

    Raises:
        EnvironmentError: NBT_USERNAME 또는 NBT_PASSWORD가 설정되지 않은 경우
    """
    # .env 파일 로드 (이미 환경변수에 값이 있으면 덮어쓰지 않음)
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)
        logger.debug(f".env 파일 로드: {ENV_PATH}")
    else:
        logger.debug(".env 파일 없음. 시스템 환경변수만 사용합니다.")

    username = os.environ.get("NBT_USERNAME", "").strip()
    password = os.environ.get("NBT_PASSWORD", "").strip()

    missing = []
    if not username:
        missing.append("NBT_USERNAME")
    if not password:
        missing.append("NBT_PASSWORD")

    if missing:
        raise EnvironmentError(
            f"필수 환경변수가 설정되지 않았습니다: {', '.join(missing)}\n"
            f"  -> .env 파일 또는 시스템 환경변수에 값을 설정하세요.\n"
            f"  -> 템플릿: {PROJECT_ROOT / '.env.example'}"
        )

    return username, password


# =================================================================
# 공개 인터페이스
# =================================================================
class AppConfig:
    """
    NBT 전체 설정을 보관하는 컨테이너입니다.

    Attributes:
        username: 장비 접속 계정
        password: 장비 접속 비밀번호
        devices: 장비 그룹별 설정 (device_type, hosts)
        backup: 백업 동작 설정 (max_retries, retry_delay, session_timeout)
        commands: 장비 그룹별 명령어 목록
    """

    def __init__(
        self,
        username: str,
        password: str,
        devices: dict[str, Any],
        backup: dict[str, Any],
        commands: dict[str, Any],
    ) -> None:
        self.username = username
        self.password = password
        self.devices = devices
        self.backup = backup
        self.commands = commands

    def __repr__(self) -> str:
        host_counts = {
            group: len(cfg.get("hosts", []))
            for group, cfg in self.devices.items()
        }
        return (
            f"AppConfig("
            f"username={self.username!r}, "
            f"devices={host_counts}, "
            f"max_retries={self.backup.get('max_retries')})"
        )


def load_config() -> AppConfig:
    """
    전체 설정을 로드하고 검증한 뒤 AppConfig 객체로 반환합니다.

    Returns:
        AppConfig 객체

    Raises:
        FileNotFoundError: 설정 파일이 없는 경우
        ValueError: 필수 키가 누락된 경우
        EnvironmentError: 계정 환경변수가 없는 경우
    """
    logger.info("설정 로드 시작")

    # 계정 정보 (환경변수 우선)
    username, password = _load_credentials()
    logger.info("계정 정보 로드 완료")

    # settings.yaml
    settings = _load_yaml(SETTINGS_PATH)
    _validate_settings(settings)
    logger.info(f"settings.yaml 로드 완료: {SETTINGS_PATH}")

    # commands.yaml
    commands = _load_yaml(COMMANDS_PATH)
    _validate_commands(commands)
    logger.info(f"commands.yaml 로드 완료: {COMMANDS_PATH}")

    config = AppConfig(
        username=username,
        password=password,
        devices=settings["devices"],
        backup=settings["backup"],
        commands=commands,
    )

    logger.info(f"설정 로드 완료: {config}")
    return config