"""
NBT (Network Backup Tools) - Config Loader
Version: 3.0

YAML 설정 파일 로드, 스키마 검증, 환경변수 기반 계정 수신 모듈.

Update History:
- ver 2.1: 최초 작성 (YAML 파서 + 스키마 검증 + 환경변수 수신)
- ver 2.3: max_workers 항목 추가
- ver 3.0: diff 섹션 파싱 추가 (noise_patterns)
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# 기본 설정 파일 경로
_SETTINGS_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"
_COMMANDS_PATH = Path(__file__).parent.parent / "config" / "commands.yaml"

# diff 섹션 기본값 (settings.yaml에 diff 섹션이 없을 경우 적용)
_DEFAULT_NOISE_PATTERNS: list[str] = [
    "uptime is",
    "Last reload",
    "Current time",
    "clock is",
    "Last input",
    "Last output",
    "Last clearing",
    "5 minute input rate",
    "5 minute output rate",
    "30 seconds input rate",
    "30 seconds output rate",
    "Last Flapped",
]


@dataclass
class AppConfig:
    """애플리케이션 전체 설정을 담는 데이터 클래스."""
    username: str
    password: str
    devices: dict                        # {'mgmt': {'device_type': ..., 'hosts': [...]}, ...}
    backup: dict                         # {'max_retries': ..., 'retry_delay': ..., ...}
    commands: dict                       # {'mgmt': [...], 'nexus': [...], 'aci': [...]}
    diff: dict = field(default_factory=dict)  # {'noise_patterns': [...]}


def load_config(
    settings_path: Path = _SETTINGS_PATH,
    commands_path: Path = _COMMANDS_PATH,
) -> AppConfig:
    """
    설정 파일을 로드하고 검증하여 AppConfig를 반환합니다.

    로드 순서:
        1. .env 파일 (있을 경우) → 환경변수로 등록
        2. 환경변수에서 계정 정보 수신 (NBT_USERNAME, NBT_PASSWORD)
        3. settings.yaml 로드 및 검증
        4. commands.yaml 로드 및 검증

    Args:
        settings_path: settings.yaml 파일 경로.
        commands_path: commands.yaml 파일 경로.

    Returns:
        AppConfig 인스턴스.

    Raises:
        FileNotFoundError: 설정 파일이 존재하지 않을 경우.
        ValueError: 필수 키가 누락되었을 경우.
        EnvironmentError: 계정 환경변수가 설정되지 않은 경우.
    """
    load_dotenv()

    username, password = _load_credentials()
    settings = _load_yaml(settings_path)
    commands = _load_yaml(commands_path)

    _validate_settings(settings)
    _validate_commands(commands)

    diff_config = _parse_diff_config(settings)

    logger.info("설정 파일 로드 완료: %s", settings_path)

    return AppConfig(
        username=username,
        password=password,
        devices=settings["devices"],
        backup=settings["backup"],
        commands=commands,
        diff=diff_config,
    )


# ------------------------------------------------------------------
# 내부 함수
# ------------------------------------------------------------------

def _load_credentials() -> tuple[str, str]:
    """
    환경변수에서 계정 정보를 로드합니다.

    Returns:
        (username, password) 튜플.

    Raises:
        EnvironmentError: 환경변수가 설정되지 않은 경우.
    """
    username = os.environ.get("NBT_USERNAME", "").strip()
    password = os.environ.get("NBT_PASSWORD", "").strip()

    if not username:
        raise EnvironmentError(
            "환경변수 NBT_USERNAME이 설정되지 않았습니다. "
            ".env 파일 또는 docker-compose.yml 환경변수를 확인하세요."
        )
    if not password:
        raise EnvironmentError(
            "환경변수 NBT_PASSWORD가 설정되지 않았습니다. "
            ".env 파일 또는 docker-compose.yml 환경변수를 확인하세요."
        )

    return username, password


def _load_yaml(path: Path) -> dict:
    """
    YAML 파일을 로드합니다.

    Args:
        path: YAML 파일 경로.

    Returns:
        파싱된 딕셔너리.

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 경우.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"설정 파일을 찾을 수 없습니다: {path}\n"
            f".example 파일을 복사하여 생성하세요."
        )

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return data or {}


def _validate_settings(settings: dict) -> None:
    """
    settings.yaml 필수 키를 검증합니다.

    Raises:
        ValueError: 필수 키 누락 또는 형식 오류.
    """
    # 최상위 필수 키
    required_top_keys = ["devices", "backup"]
    for key in required_top_keys:
        if key not in settings:
            raise ValueError(f"settings.yaml에 필수 키 '{key}'가 없습니다.")

    # devices 하위 검증
    devices = settings["devices"]
    if not isinstance(devices, dict) or not devices:
        raise ValueError("settings.yaml의 'devices' 섹션이 비어 있거나 형식이 잘못되었습니다.")

    for group_name, group_info in devices.items():
        if "device_type" not in group_info:
            raise ValueError(
                f"devices.{group_name}에 'device_type' 키가 없습니다."
            )
        if "hosts" not in group_info or not group_info["hosts"]:
            raise ValueError(
                f"devices.{group_name}에 'hosts' 키가 없거나 비어 있습니다."
            )

    # backup 하위 검증
    backup = settings["backup"]
    required_backup_keys = ["max_retries", "retry_delay", "session_timeout", "max_workers"]
    for key in required_backup_keys:
        if key not in backup:
            raise ValueError(f"settings.yaml backup 섹션에 '{key}' 키가 없습니다.")


def _validate_commands(commands: dict) -> None:
    """
    commands.yaml 필수 키를 검증합니다.

    Raises:
        ValueError: 필수 키 누락 또는 형식 오류.
    """
    if not isinstance(commands, dict) or not commands:
        raise ValueError("commands.yaml이 비어 있거나 형식이 잘못되었습니다.")

    for group_name, command_list in commands.items():
        if not isinstance(command_list, list) or not command_list:
            raise ValueError(
                f"commands.yaml의 '{group_name}' 섹션이 비어 있거나 리스트 형식이 아닙니다."
            )


def _parse_diff_config(settings: dict) -> dict:
    """
    settings.yaml의 diff 섹션을 파싱합니다.
    섹션이 없으면 기본값을 반환합니다.

    Returns:
        {'noise_patterns': [...]} 딕셔너리.
    """
    diff_section = settings.get("diff", {})

    noise_patterns = diff_section.get("noise_patterns", _DEFAULT_NOISE_PATTERNS)

    if not isinstance(noise_patterns, list):
        logger.warning(
            "diff.noise_patterns 형식이 잘못되었습니다 — 기본값을 사용합니다."
        )
        noise_patterns = _DEFAULT_NOISE_PATTERNS

    logger.info("Diff 노이즈 필터 패턴 수: %d", len(noise_patterns))

    return {"noise_patterns": noise_patterns}