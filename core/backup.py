"""
NBT (Network Backup Tools) - Main Backup Module
Version: 2.3

Update History:
- ver 1.0 (2025/09/15): Netmiko 라이브러리로 마이그레이션, 다중 장비 그룹 지원
- ver 1.1 (2025/09/15): logging 추가, 재시도 5회
- ver 1.2 (2025/09/22): session_timeout 30초로 변경
- ver 2.0 (2026/03/09): Docker 환경 적응, pathlib 전환, 함수 기반 구조
- ver 2.1 (2026/03/10): YAML 설정 전환, config_loader 도입, 예외 타입 세분화
- ver 2.2 (2026/03/10): SQLite DB 연동, 백업 이력 저장
- ver 2.3 (2026/03/10): ThreadPoolExecutor 병렬 실행, max_workers 설정 추가
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from utils.config_loader import load_config, AppConfig
from utils.db_manager import DBManager
from utils.folder_create import create_backup_folder, create_log_folder


# ------------------------------------------------------------------
# 내부 데이터 구조
# ------------------------------------------------------------------
@dataclass
class DeviceTask:
    """병렬 실행 단위 — 장비 1대의 작업 정보."""
    host: str
    device_type: str
    os_type: str          # MGMT / NEXUS / ACI (DB 저장 및 로그용)
    commands: list[str]


@dataclass
class BackupResult:
    """개별 장비 백업 결과."""
    host: str
    hostname: str
    os_type: str
    device_type: str
    status: str           # 'SUCCESS' | 'FAIL'
    file_path: str | None
    duration_sec: float
    error_msg: str | None


# ------------------------------------------------------------------
# 로깅 설정
# ------------------------------------------------------------------
def setup_logging(log_folder: Path) -> None:
    """로그 폴더에 파일 핸들러를 설정합니다."""
    log_file = log_folder / 'nbt_backup.log'
    logging.basicConfig(
        filename=str(log_file),
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s',
        encoding='utf-8'
    )


# ------------------------------------------------------------------
# 장비 목록 평탄화
# ------------------------------------------------------------------
def _build_device_tasks(config: AppConfig) -> list[DeviceTask]:
    """
    config.devices와 config.commands를 조합하여
    DeviceTask 리스트로 평탄화합니다.

    순서: MGMT → NEXUS → ACI (설정 파일 정의 순서 유지)

    Returns:
        list[DeviceTask]: 병렬 실행 대상 장비 목록
    """
    tasks: list[DeviceTask] = []
    for group_name, group_info in config.devices.items():
        device_type: str = group_info['device_type']
        hosts: list[str] = group_info.get('hosts', [])
        commands: list[str] = config.commands.get(group_name, [])

        if not hosts:
            logging.warning(f"[{group_name}] 장비 목록이 비어 있습니다. 건너뜁니다.")
            continue
        if not commands:
            logging.warning(f"[{group_name}] 명령어 목록이 비어 있습니다. 건너뜁니다.")
            continue

        for host in hosts:
            tasks.append(DeviceTask(
                host=host,
                device_type=device_type,
                os_type=group_name.upper(),
                commands=commands,
            ))

    return tasks


# ------------------------------------------------------------------
# 개별 장비 백업 (스레드 실행 단위)
# ------------------------------------------------------------------
def backup_device(
    task: DeviceTask,
    config: AppConfig,
    backup_folder: Path,
    db: DBManager,
) -> BackupResult:
    """
    개별 장비 1대의 백업을 수행합니다.
    ThreadPoolExecutor의 각 스레드에서 호출됩니다.

    예외 처리 3계층:
        NetmikoAuthenticationException : 인증 실패 → 즉시 중단 (재시도 무의미)
        NetmikoTimeoutException         : 타임아웃 → 재시도
        Exception                       : 기타 → 재시도 + 스택 로깅

    Args:
        task          : 장비 정보 (host, device_type, os_type, commands)
        config        : 전체 설정 (계정, 백업 파라미터)
        backup_folder : 백업 파일 저장 경로
        db            : DBManager 인스턴스 (thread-safe)

    Returns:
        BackupResult: 백업 결과 (SUCCESS / FAIL)
    """
    host = task.host
    max_retries: int = config.backup.get('max_retries', 5)
    retry_delay: int = config.backup.get('retry_delay', 10)
    session_timeout: int = config.backup.get('session_timeout', 30)

    device_params: dict[str, Any] = {
        'device_type': task.device_type,
        'host': host,
        'username': config.username,
        'password': config.password,
        'session_timeout': session_timeout,
    }

    last_error: str = ""
    hostname: str = host   # 연결 전 fallback

    for attempt in range(1, max_retries + 1):
        start_time = time.time()
        try:
            logging.info(f"[{task.os_type}] 백업 시작: {host} (시도 {attempt}/{max_retries})")

            with ConnectHandler(**device_params) as net_connect:
                hostname = net_connect.find_prompt().strip('#>')
                logging.info(f"[{host} >> {hostname}] 연결 성공.")

                full_output = ""
                for command in task.commands:
                    logging.debug(f"[{host} >> {hostname}] 명령어 실행: {command}")
                    output = net_connect.send_command(command)
                    full_output += f"\n\n{'='*10} {command} {'='*10}\n\n{output}"

                file_path = backup_folder / f"{hostname}.txt"
                file_path.write_text(full_output.strip(), encoding='utf-8')

                duration = round(time.time() - start_time, 2)
                logging.info(
                    f"[{host} >> {hostname}] 백업 완료: {file_path} "
                    f"({duration}초)"
                )

                result = BackupResult(
                    host=host,
                    hostname=hostname,
                    os_type=task.os_type,
                    device_type=task.device_type,
                    status='SUCCESS',
                    file_path=str(file_path),
                    duration_sec=duration,
                    error_msg=None,
                )
                db.save_result(
                    hostname=hostname,
                    ip=host,
                    device_type=task.device_type,
                    os_type=task.os_type,
                    status='SUCCESS',
                    file_path=str(file_path),
                    duration_sec=duration,
                )
                return result

        except NetmikoAuthenticationException as e:
            # 인증 실패: 재시도해도 해결되지 않으므로 즉시 중단
            last_error = f"인증 실패: {e}"
            logging.error(f"[{host}] {last_error} — 재시도 중단.")
            break

        except NetmikoTimeoutException as e:
            last_error = f"타임아웃: {e}"
            logging.warning(f"[{host}] {last_error} (시도 {attempt}/{max_retries})")
            if attempt < max_retries:
                logging.info(f"[{host}] {retry_delay}초 후 재시도합니다...")
                time.sleep(retry_delay)

        except Exception as e:
            last_error = str(e)
            logging.error(
                f"[{host}] 예외 발생: {e} (시도 {attempt}/{max_retries})",
                exc_info=True
            )
            if attempt < max_retries:
                logging.info(f"[{host}] {retry_delay}초 후 재시도합니다...")
                time.sleep(retry_delay)

    # 모든 재시도 실패
    duration = round(time.time() - start_time, 2)
    logging.error(f"[{host}] 최종 연결 실패. 오류: {last_error}")

    result = BackupResult(
        host=host,
        hostname=hostname,
        os_type=task.os_type,
        device_type=task.device_type,
        status='FAIL',
        file_path=None,
        duration_sec=duration,
        error_msg=last_error,
    )
    db.save_result(
        hostname=hostname,
        ip=host,
        device_type=task.device_type,
        os_type=task.os_type,
        status='FAIL',
        duration_sec=duration,
        error_msg=last_error,
    )
    return result


# ------------------------------------------------------------------
# 전체 백업 실행
# ------------------------------------------------------------------
def run_backup() -> None:
    """
    전체 백업 작업을 병렬로 실행합니다.

    실행 흐름:
        1. config 로드 (YAML + .env)
        2. 폴더 / 로그 / DB 초기화
        3. DeviceTask 리스트 생성 (전체 장비 평탄화)
        4. ThreadPoolExecutor로 병렬 백업 실행
        5. 결과 집계 및 DB 업데이트
        6. 집계 출력 후 DB 종료
    """
    config = load_config()
    backup_folder = create_backup_folder()
    log_folder = create_log_folder()
    setup_logging(log_folder)

    db_path = backup_folder.parent / 'nbt_history.db'
    db = DBManager(db_path)
    db.initialize()
    run_id = db.start_run()

    logging.info("===== NBT 백업 스크립트 시작 (v2.3 병렬 실행) =====")
    logging.info(f"백업 저장 경로: {backup_folder}")

    device_tasks = _build_device_tasks(config)
    total = len(device_tasks)
    max_workers: int = config.backup.get('max_workers', 5)

    logging.info(
        f"대상 장비: {total}대 | max_workers: {max_workers}"
    )
    print(f"\n  대상 장비: {total}대 | 동시 실행 스레드: {max_workers}\n")

    results: list[BackupResult] = []

    try:
        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix='nbt-worker'
        ) as executor:
            future_to_task: dict[Future, DeviceTask] = {
                executor.submit(backup_device, task, config, backup_folder, db): task
                for task in device_tasks
            }

            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    result: BackupResult = future.result()
                    results.append(result)
                    status_label = "성공" if result.status == 'SUCCESS' else "실패"
                    print(
                        f"  [{result.os_type}] {result.host} >> {result.hostname}"
                        f" — {status_label} ({result.duration_sec}초)"
                    )
                except Exception as e:
                    # future.result()에서 예외가 올라오는 경우 (예상치 못한 오류)
                    logging.error(
                        f"[{task.host}] future 처리 중 예외 발생: {e}",
                        exc_info=True
                    )
                    results.append(BackupResult(
                        host=task.host,
                        hostname=task.host,
                        os_type=task.os_type,
                        device_type=task.device_type,
                        status='FAIL',
                        file_path=None,
                        duration_sec=0.0,
                        error_msg=str(e),
                    ))

    finally:
        success = sum(1 for r in results if r.status == 'SUCCESS')
        fail = total - success

        db.finish_run(total=total, success=success, fail=fail)
        db.close()

        summary = (
            f"\n  {'='*50}\n"
            f"  백업 완료 | 전체: {total}  성공: {success}  실패: {fail}\n"
            f"  {'='*50}"
        )
        print(summary)
        logging.info(
            f"===== NBT 백업 스크립트 종료 "
            f"| 전체: {total}  성공: {success}  실패: {fail} ====="
        )


if __name__ == "__main__":
    run_backup()