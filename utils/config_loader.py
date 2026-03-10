"""
NBT (Network Backup Tools) - Config Loader
- YAML 설정 파일 파싱 및 스키마 검증
- 계정 정보는 환경변수 우선, .env 파일 fallback
- settings.yaml 절대 경로는 NBT_SETTINGS_PATH 환경변수로 오버라이드 가능

로드 파일:
    - config/settings.yaml  : 장비 목록, 백업 설정, Diff 설정, 알림 설정
    - config/commands.yaml  : 장비 그룹별 명령어 목록

환경변수:
    필수: NBT_USERNAME, NBT_PASSWORD
    선택: NBT_BACKUP_ROOT, NBT_SETTINGS_PATH
    알림: NBT_SLACK_WEBHOOK, NBT_SMTP_USER, NBT_SMTP_PASSWORD
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# 프로젝트 루트 기준 경로
_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_SETTINGS_PATH = _PROJECT_ROOT / "config" / "settings.yaml"
_DEFAULT_COMMANDS_PATH = _PROJECT_ROOT / "config" / "commands.yaml"


@dataclass
class AppConfig:
    """애플리케이션 전체 설정을 담는 데이터 클래스."""

    username: str
    password: str
    devices: dict        # {'mgmt': {'device_type': ..., 'hosts': [...]}, ...}
    backup: dict         # {'max_retries': ..., 'retry_delay': ..., 'session_timeout': ..., 'max_workers': ...}
    commands: dict       # {'mgmt': [...], 'nexus': [...], 'aci': [...]}
    diff: dict           # {'noise_patterns': [...]}
    notify: dict = field(default_factory=dict)  # {'slack': {...}, 'email': {...}, 'events': {...}}


def load_config(
    settings_path: Optional[Path] = None,
    commands_path: Optional[Path] = None,
) -> AppConfig:
    """설정 파일을 로드하고 AppConfig 인스턴스를 반환합니다.

    Args:
        settings_path: settings.yaml 경로 (기본: config/settings.yaml)
        commands_path: commands.yaml 경로 (기본: config/commands.yaml)

    Returns:
        AppConfig: 검증 완료된 설정 객체

    Raises:
        FileNotFoundError: 설정 파일을 찾을 수 없는 경우
        ValueError: 필수 설정 키가 없거나 형식이 잘못된 경우
        EnvironmentError: 필수 환경변수가 없는 경우
    """
    # .env 파일 로드 (환경변수에 이미 있으면 덮어쓰지 않음)
    load_dotenv(override=False)

    # 파일 경로 결정 (환경변수 > 인자 > 기본값)
    s_path = Path(
        os.environ.get("NBT_SETTINGS_PATH", str(settings_path or _DEFAULT_SETTINGS_PATH))
    )
    c_path = commands_path or _DEFAULT_COMMANDS_PATH

    # 파일 존재 확인
    if not s_path.exists():
        raise FileNotFoundError(
            f"settings.yaml을 찾을 수 없습니다: {s_path}\n"
            f"  → settings.yaml.example을 복사하여 settings.yaml을 생성하세요."
        )
    if not c_path.exists():
        raise FileNotFoundError(f"commands.yaml을 찾을 수 없습니다: {c_path}")

    # YAML 파싱
    with s_path.open(encoding="utf-8") as f:
        settings: dict = yaml.safe_load(f) or {}
    with c_path.open(encoding="utf-8") as f:
        commands_raw: dict = yaml.safe_load(f) or {}

    # 각 섹션 검증 및 파싱
    username, password = _load_credentials()
    devices = _validate_settings(settings)
    backup = _parse_backup_config(settings)
    commands = _validate_commands(commands_raw)
    diff = _parse_diff_config(settings)
    notify = _parse_notify_config(settings)

    logger.info(
        f"설정 로드 완료 | "
        f"장비 그룹: {list(devices.keys())} | "
        f"max_workers: {backup.get('max_workers', 1)}"
    )

    return AppConfig(
        username=username,
        password=password,
        devices=devices,
        backup=backup,
        commands=commands,
        diff=diff,
        notify=notify,
    )


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _load_credentials() -> tuple[str, str]:
    """환경변수에서 계정 정보를 로드합니다."""
    username = os.environ.get("NBT_USERNAME", "")
    password = os.environ.get("NBT_PASSWORD", "")

    if not username or not password:
        raise EnvironmentError(
            "NBT_USERNAME 또는 NBT_PASSWORD 환경변수가 설정되지 않았습니다.\n"
            "  → .env.example을 참고하여 .env 파일을 생성하거나 환경변수를 설정하세요."
        )
    return username, password


def _validate_settings(settings: dict) -> dict:
    """devices 섹션을 검증하고 반환합니다."""
    devices_raw: Any = settings.get("devices")
    if not devices_raw or not isinstance(devices_raw, dict):
        raise ValueError(
            "settings.yaml에 'devices' 섹션이 없거나 형식이 잘못되었습니다."
        )

    devices: dict = {}
    for group_name, group_data in devices_raw.items():
        if not isinstance(group_data, dict):
            raise ValueError(
                f"devices.{group_name}: dict 형식이어야 합니다."
            )
        device_type = group_data.get("device_type")
        hosts = group_data.get("hosts")

        if not device_type:
            raise ValueError(
                f"devices.{group_name}: 'device_type' 키가 없습니다."
            )
        if not hosts or not isinstance(hosts, list):
            raise ValueError(
                f"devices.{group_name}: 'hosts' 키가 없거나 리스트가 아닙니다."
            )
        if len(hosts) == 0:
            raise ValueError(
                f"devices.{group_name}: 'hosts' 리스트가 비어 있습니다."
            )

        devices[group_name] = {
            "device_type": device_type,
            "hosts": hosts,
        }

    return devices


def _parse_backup_config(settings: dict) -> dict:
    """backup 섹션을 파싱합니다. 누락된 키는 기본값으로 채웁니다."""
    backup_raw = settings.get("backup", {})
    return {
        "max_retries":      int(backup_raw.get("max_retries", 5)),
        "retry_delay":      int(backup_raw.get("retry_delay", 10)),
        "session_timeout":  int(backup_raw.get("session_timeout", 30)),
        "max_workers":      int(backup_raw.get("max_workers", 4)),
    }


def _validate_commands(commands_raw: dict) -> dict:
    """commands.yaml을 검증하고 반환합니다."""
    if not commands_raw or not isinstance(commands_raw, dict):
        raise ValueError("commands.yaml이 비어 있거나 형식이 잘못되었습니다.")

    for group_name, cmd_list in commands_raw.items():
        if not isinstance(cmd_list, list) or len(cmd_list) == 0:
            raise ValueError(
                f"commands.yaml: '{group_name}' 명령어 목록이 비어 있거나 "
                f"리스트가 아닙니다."
            )
    return commands_raw


def _parse_diff_config(settings: dict) -> dict:
    """diff 섹션을 파싱합니다. 섹션이 없으면 기본값을 반환합니다."""
    diff_raw = settings.get("diff", {})
    default_noise = [
        r"Last configuration change",
        r"Current configuration",
        r"\d{2}:\d{2}:\d{2}",
    ]
    return {
        "noise_patterns": diff_raw.get("noise_patterns", default_noise),
    }


def _parse_notify_config(settings: dict) -> dict:
    """notify 섹션을 파싱합니다. 섹션이 없으면 빈 dict를 반환합니다.

    민감 정보(Webhook URL, SMTP 계정)는 notifier.build_notifier()에서
    환경변수 오버라이드를 수행합니다.
    """
    notify_raw = settings.get("notify", {})
    if not notify_raw:
        logger.debug("settings.yaml에 notify 섹션 없음 — 알림 비활성화")
        return {}

    # 구조만 검증, 값은 그대로 전달 (환경변수 오버라이드는 notifier.py에서 처리)
    return {
        "slack":  notify_raw.get("slack", {}),
        "email":  notify_raw.get("email", {}),
        "events": notify_raw.get("events", {}),
    }