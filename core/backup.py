"""
NBT (Network Backup Tools) - Main Backup Module
Version: 2.1

Update History:
    ver 1.0 (2025/09/15): Netmiko 마이그레이션, 다중 장비 그룹 지원
    ver 1.1 (2025/09/15): logging 추가, 재시도 5회로 증가
    ver 1.2 (2025/09/22): session_timeout 30초로 변경
    ver 2.0 (2026/03/09): Docker 환경 적용, pathlib 전환, 함수 기반 구조
    ver 2.1 (2026/03/09): YAML 설정 전환, config_loader 도입, .env 보안 적용
"""

import logging
import time
from pathlib import Path

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException

from utils.config_loader import AppConfig, load_config
from utils.folder_create import create_backup_folder, create_log_folder

logger = logging.getLogger(__name__)


# =================================================================
# 로깅 설정
# =================================================================
def setup_logging(log_folder: Path) -> None:
    """로그 폴더에 파일 핸들러를 추가합니다."""
    log_file = log_folder / "nbt_backup.log"
    logging.basicConfig(
        filename=str(log_file),
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        encoding="utf-8",
    )


# =================================================================
# 개별 장비 백업
# =================================================================
def backup_device(
    host: str,
    device_type: str,
    commands: list[str],
    backup_folder: Path,
    username: str,
    password: str,
    max_retries: int,
    retry_delay: int,
    session_timeout: int,
) -> None:
    """
    개별 장비에 SSH 접속하여 명령어를 실행하고 결과를 파일로 저장합니다.

    Args:
        host: 장비 IP 주소
        device_type: Netmiko 장비 타입 (cisco_ios / cisco_nxos)
        commands: 실행할 명령어 목록
        backup_folder: 백업 파일 저장 경로
        username: 장비 접속 계정
        password: 장비 접속 비밀번호
        max_retries: 최대 재시도 횟수
        retry_delay: 재시도 간격 (초)
        session_timeout: SSH 세션 타임아웃 (초)
    """
    device_params = {
        "device_type": device_type,
        "host": host,
        "username": username,
        "password": password,
        "session_timeout": session_timeout,
    }

    for attempt in range(max_retries):
        try:
            print(f"=============== 백업 시작: {host} (시도 {attempt + 1}/{max_retries}) ===============")
            logger.info(f"백업 시작: {host} (시도 {attempt + 1}/{max_retries})")

            with ConnectHandler(**device_params) as net_connect:
                hostname = net_connect.find_prompt().strip("#>")
                print(f"[{host} >> {hostname}] 연결 성공")
                logger.info(f"[{host} >> {hostname}] 연결 성공")

                file_path = backup_folder / f"{hostname}.txt"
                print(f"[{host} >> {hostname}] 저장 경로: {file_path}")

                full_output = ""
                for command in commands:
                    print(f"[{host} >> {hostname}] 명령어 실행: {command}")
                    output = net_connect.send_command(command)
                    full_output += f"\n\n{'=' * 10} {command} {'=' * 10}\n\n{output}"

                file_path.write_text(full_output.strip(), encoding="utf-8")

                print(f"[{host} >> {hostname}] 백업 완료: {file_path}")
                logger.info(f"[{host} >> {hostname}] 백업 파일 저장 성공: {file_path}")
                return  # 성공 시 재시도 루프 종료

        except NetmikoAuthenticationException as e:
            # 인증 실패는 재시도해도 의미 없으므로 즉시 중단
            print(f"[{host}] 인증 실패: 계정 정보를 확인하세요.")
            logger.error(f"[{host}] 인증 실패. 재시도 중단. 오류: {e}")
            break

        except NetmikoTimeoutException as e:
            print(f"[{host}] 접속 타임아웃 (시도 {attempt + 1}/{max_retries})")
            logger.warning(f"[{host}] 접속 타임아웃. 오류: {e}")
            _handle_retry(host, attempt, max_retries, retry_delay)

        except Exception as e:
            print(f"[{host}] 오류 발생: {e}")
            logger.error(f"[{host}] 백업 실패. 오류: {e}")
            _handle_retry(host, attempt, max_retries, retry_delay)

    print(f"[{host}] 작업 완료 및 연결 종료\n")


def _handle_retry(host: str, attempt: int, max_retries: int, retry_delay: int) -> None:
    """재시도 대기 또는 최종 실패 처리를 수행합니다."""
    if attempt < max_retries - 1:
        print(f"[{host}] {retry_delay}초 후 재시도합니다...")
        logger.warning(f"[{host}] {retry_delay}초 후 재시도합니다...")
        time.sleep(retry_delay)
    else:
        print(f"[{host}] 최종 연결에 실패했습니다.")
        logger.error(f"[{host}] 최종 연결 실패.")


# =================================================================
# 백업 실행 진입점
# =================================================================
def run_backup() -> None:
    """전체 백업 작업을 실행합니다."""
    # 폴더 생성
    backup_folder = create_backup_folder()
    log_folder = create_log_folder()
    setup_logging(log_folder)

    # 설정 로드 (YAML + 환경변수)
    try:
        config: AppConfig = load_config()
    except (FileNotFoundError, ValueError, EnvironmentError) as e:
        print(f"\n[설정 오류] {e}")
        logger.critical(f"설정 로드 실패: {e}")
        return

    logger.info("===== NBT 백업 스크립트 시작 =====")
    logger.info(f"백업 저장 경로: {backup_folder}")

    # 장비 그룹 순회
    for group_name, group_cfg in config.devices.items():
        device_type: str = group_cfg["device_type"]
        hosts: list[str] = group_cfg["hosts"]
        commands: list[str] = config.commands.get(group_name, [])

        if not commands:
            print(f"[{group_name.upper()}] commands.yaml에 명령어가 없습니다. 건너뜁니다.")
            logger.warning(f"[{group_name}] 명령어 목록 없음. 건너뜀.")
            continue

        print(f"\n############################################################")
        print(f"## 시작: {group_name.upper()} 장비 그룹 백업")
        print(f"############################################################\n")
        logger.info(f"===== 시작: {group_name.upper()} 장비 그룹 백업 =====")

        for host in hosts:
            backup_device(
                host=host,
                device_type=device_type,
                commands=commands,
                backup_folder=backup_folder,
                username=config.username,
                password=config.password,
                max_retries=config.backup["max_retries"],
                retry_delay=config.backup["retry_delay"],
                session_timeout=config.backup["session_timeout"],
            )

    logger.info("===== NBT 백업 스크립트 종료 =====")


if __name__ == "__main__":
    run_backup()