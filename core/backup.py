"""
NBT (Network Backup Tools) - Main Backup Module
- Version: 3.1
- Cisco IOS, IOS-XE, NX-OS 장비의 설정을 자동으로 백업하는 모듈

Update History:
- ver 1.0 (2025/09/15): Netmiko 마이그레이션, 다중 장비 그룹 지원
- ver 1.1 (2025/09/15): logging 추가, 재시도 5회
- ver 1.2 (2025/09/22): session_timeout 30초
- ver 2.0          : Docker 환경 적응, pathlib 전환, 함수 기반 구조
- ver 2.1          : YAML 설정 전환, 보안 적용
- ver 2.2          : SQLite DB 연동, 백업 이력 저장
- ver 2.3          : ThreadPoolExecutor 병렬 실행
- ver 3.0          : Config Diff 감지, 노이즈 필터링
- ver 3.1          : Typer CLI 연동, group_filter, dry_run, Notifier 연동
"""

import difflib
import logging
import queue
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)

from utils.config_loader import AppConfig, load_config
from utils.db_manager import DBManager
from utils.folder_create import create_backup_folder, create_log_folder
from utils.notifier import Notifier, build_notifier

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------

@dataclass
class DeviceTask:
    host: str
    device_type: str
    group_name: str
    commands: list[str]


@dataclass
class BackupResult:
    task: DeviceTask
    status: str              # 'SUCCESS' | 'FAIL'
    hostname: str
    file_path: Optional[Path]
    duration_sec: float
    error_msg: str


@dataclass
class DiffResult:
    hostname: str
    ip: str
    diff_lines: int
    diff_content: str
    previous_file: str
    current_file: str


# ------------------------------------------------------------------
# Logging setup
# ------------------------------------------------------------------

def _setup_logging(log_folder: Path) -> None:
    """파일 + 콘솔 핸들러로 로깅을 설정합니다."""
    log_file = log_folder / "nbt_backup.log"
    formatter = logging.Formatter(
        "%(asctime)s - [%(threadName)s] - %(levelname)s - %(message)s"
    )

    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.WARNING)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    # 중복 핸들러 방지
    if not root_logger.handlers:
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
    else:
        root_logger.addHandler(file_handler)


# ------------------------------------------------------------------
# Task builder
# ------------------------------------------------------------------

def _build_device_tasks(
    config: AppConfig,
    group_filter: Optional[str] = None,
) -> list[DeviceTask]:
    """전체 장비 그룹을 단일 DeviceTask 리스트로 평탄화합니다.

    Args:
        config: AppConfig 인스턴스
        group_filter: 특정 그룹만 실행할 경우 그룹명 (mgmt/nexus/aci)
    """
    tasks: list[DeviceTask] = []

    for group_name, group_data in config.devices.items():
        if group_filter and group_name.lower() != group_filter.lower():
            continue

        device_type = group_data["device_type"]
        hosts = group_data["hosts"]
        commands = config.commands.get(group_name, [])

        if not commands:
            logger.warning(f"[{group_name}] commands.yaml에 명령어 없음 — 건너뜀")
            continue

        for host in hosts:
            tasks.append(
                DeviceTask(
                    host=host,
                    device_type=device_type,
                    group_name=group_name,
                    commands=commands,
                )
            )

    return tasks


# ------------------------------------------------------------------
# Diff helpers
# ------------------------------------------------------------------

def _filter_noise(lines: list[str], noise_patterns: list[str]) -> list[str]:
    """동적 상태값 라인을 제거합니다.

    원본 파일은 변경하지 않으며, Diff 비교 전용으로만 사용합니다.
    """
    filtered: list[str] = []
    compiled = [re.compile(p, re.IGNORECASE) for p in noise_patterns]
    for line in lines:
        if not any(pat.search(line) for pat in compiled):
            filtered.append(line)
    return filtered


def _run_diff(
    task: DeviceTask,
    result: BackupResult,
    db: DBManager,
    run_id: int,
    noise_patterns: list[str],
    notifier: Notifier,
) -> Optional[DiffResult]:
    """이전 백업과 현재 백업을 비교하여 Config Diff를 감지합니다.

    흐름:
        1. 첫 번째 백업 → None 반환 (비교 대상 없음)
        2. 변경 없음   → None 반환 ([OK] 로깅)
        3. 변경 있음   → DiffResult 반환 + DB 저장 + 알림 전송
    """
    if result.status != "SUCCESS" or result.file_path is None:
        return None

    prev_path_str = db.get_last_backup_path(result.hostname, run_id)
    if not prev_path_str:
        logger.info(f"[{result.hostname}] 첫 번째 백업 — Diff 건너뜀")
        return None

    prev_path = Path(prev_path_str)
    if not prev_path.exists():
        logger.warning(f"[{result.hostname}] 이전 백업 파일 없음: {prev_path}")
        return None

    prev_lines = _filter_noise(
        prev_path.read_text(encoding="utf-8").splitlines(), noise_patterns
    )
    curr_lines = _filter_noise(
        result.file_path.read_text(encoding="utf-8").splitlines(), noise_patterns
    )

    diff = list(difflib.unified_diff(prev_lines, curr_lines, lineterm=""))

    if not diff:
        logger.info(f"[{result.hostname}] Config 변경 없음 [OK]")
        return None

    diff_content = "\n".join(diff)
    diff_lines = len(
        [line for line in diff if line.startswith(("+", "-"))
         and not line.startswith(("+++", "---"))]
    )

    logger.info(f"[{result.hostname}] Config 변경 감지 [DIFF] {diff_lines}줄")

    db.save_diff(
        run_id=run_id,
        hostname=result.hostname,
        ip=task.host,
        previous_file=str(prev_path),
        current_file=str(result.file_path),
        diff_lines=diff_lines,
        diff_content=diff_content,
    )

    notifier.send_diff_detected(result.hostname, task.host, diff_lines)

    return DiffResult(
        hostname=result.hostname,
        ip=task.host,
        diff_lines=diff_lines,
        diff_content=diff_content,
        previous_file=str(prev_path),
        current_file=str(result.file_path),
    )


# ------------------------------------------------------------------
# Device backup
# ------------------------------------------------------------------

def _backup_device(
    task: DeviceTask,
    backup_folder: Path,
    config: AppConfig,
    db: DBManager,
    run_id: int,
    notifier: Notifier,
) -> BackupResult:
    """개별 장비 백업을 수행합니다.

    예외 계층:
        - NetmikoAuthenticationException : 즉시 중단 (재시도 무의미)
        - NetmikoTimeoutException         : 재시도
        - Exception                       : 재시도 + 스택 로깅
    """
    max_retries: int = config.backup["max_retries"]
    retry_delay: int = config.backup["retry_delay"]

    device_params = {
        "device_type":     task.device_type,
        "host":            task.host,
        "username":        config.username,
        "password":        config.password,
        "session_timeout": config.backup["session_timeout"],
    }

    hostname = task.host
    start_time = time.monotonic()
    last_error = ""

    for attempt in range(max_retries):
        try:
            logger.info(
                f"백업 시작: {task.host} "
                f"(시도 {attempt + 1}/{max_retries})"
            )

            with ConnectHandler(**device_params) as net_connect:
                hostname = net_connect.find_prompt().strip("#>")
                logger.info(f"[{task.host} >> {hostname}] 연결 성공")

                file_path = backup_folder / f"{hostname}.txt"
                full_output = ""

                for command in task.commands:
                    logger.info(
                        f"[{task.host} >> {hostname}] 명령어 실행: {command}"
                    )
                    output = net_connect.send_command(command)
                    full_output += (
                        f"\n\n{'=' * 10} {command} {'=' * 10}\n\n{output}"
                    )

                file_path.write_text(full_output.strip(), encoding="utf-8")
                duration = time.monotonic() - start_time
                logger.info(
                    f"[{task.host} >> {hostname}] 백업 완료: "
                    f"{file_path} ({duration:.1f}s)"
                )

                result = BackupResult(
                    task=task,
                    status="SUCCESS",
                    hostname=hostname,
                    file_path=file_path,
                    duration_sec=round(duration, 2),
                    error_msg="",
                )
                db.save_result(
                    run_id=run_id,
                    hostname=hostname,
                    ip=task.host,
                    device_type=task.device_type,
                    os_type=task.device_type,
                    status="SUCCESS",
                    file_path=str(file_path),
                    duration_sec=result.duration_sec,
                    error_msg=None,
                )
                return result

        except NetmikoAuthenticationException as e:
            last_error = f"인증 실패: {e}"
            logger.error(f"[{task.host}] {last_error} — 재시도 중단")
            break

        except NetmikoTimeoutException as e:
            last_error = f"연결 타임아웃: {e}"
            logger.warning(f"[{task.host}] {last_error}")

        except Exception as e:
            last_error = str(e)
            logger.error(f"[{task.host}] 백업 실패: {e}", exc_info=True)

        if attempt < max_retries - 1:
            logger.warning(
                f"[{task.host}] {retry_delay}초 후 재시도 "
                f"({attempt + 2}/{max_retries})..."
            )
            time.sleep(retry_delay)

    # 모든 재시도 소진
    duration = time.monotonic() - start_time
    logger.error(f"[{task.host}] 최종 연결 실패")

    result = BackupResult(
        task=task,
        status="FAIL",
        hostname=hostname,
        file_path=None,
        duration_sec=round(duration, 2),
        error_msg=last_error,
    )
    db.save_result(
        run_id=run_id,
        hostname=hostname,
        ip=task.host,
        device_type=task.device_type,
        os_type=task.device_type,
        status="FAIL",
        file_path=None,
        duration_sec=result.duration_sec,
        error_msg=last_error,
    )
    # 장비 개별 실패 알림
    notifier.send_device_failure(hostname, task.host, last_error)
    return result


# ------------------------------------------------------------------
# Public entry points
# ------------------------------------------------------------------

def run_backup(
    group_filter: Optional[str] = None,
    dry_run: bool = False,
    log_queue: Optional[queue.Queue] = None,
) -> None:
    """전체 백업 작업을 실행합니다.

    Args:
        group_filter: 특정 그룹만 실행 (mgmt/nexus/aci). None이면 전체 실행.
        dry_run: True이면 설정 검증만 수행하고 실제 접속하지 않습니다.
    """
    config = load_config()
    tasks = _build_device_tasks(config, group_filter)

    # dry-run: 설정 검증만 수행
    if dry_run:
        _print_dry_run(tasks, config)
        return

    if not tasks:
        msg = f"실행할 장비가 없습니다. group_filter='{group_filter}'"
        if log_queue:
            log_queue.put(msg)
        logger.warning(f"실행할 장비 없음: group_filter={group_filter}")
        return

    backup_folder = create_backup_folder()
    log_folder = create_log_folder()
    _setup_logging(log_folder)

    db_path = backup_folder.parent / "nbt_history.db"
    db = DBManager(db_path)
    db.initialize()

    notifier = build_notifier(config.notify)
    run_id = db.start_run()
    results: list[BackupResult] = []

    logger.info(f"===== NBT 백업 시작 | run_id={run_id} =====")
    logger.info(f"백업 저장 경로: {backup_folder}")
    if group_filter:
        logger.info(f"그룹 필터: {group_filter}")

    try:
        # 병렬 백업 실행
        max_workers: int = config.backup["max_workers"]
        with ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="nbt-worker",
        ) as executor:
            future_map = {
                executor.submit(
                    _backup_device, task, backup_folder, config, db, run_id, notifier
                ): task
                for task in tasks
            }
            for future in as_completed(future_map):
                try:
                    result = future.result()
                    results.append(result)
                    status_icon = "OK  " if result.status == "SUCCESS" else "FAIL"
                    msg = (
                        f"[{status_icon}] {result.hostname} "
                        f"{result.task.host} {result.duration_sec:.1f}s"
                    )
                    if log_queue:
                        log_queue.put(msg)
                    else:
                        print(f"  {msg}")  # CLI 모드
                except Exception as e:
                    task = future_map[future]
                    logger.error(f"[{task.host}] Future 예외: {e}", exc_info=True)

        # Diff 분석 (병렬 완료 후 순차 실행)
        noise_patterns = config.diff.get("noise_patterns", [])
        diff_results: list[DiffResult] = []

        for result in results:
            diff = _run_diff(
                result.task, result, db, run_id, noise_patterns, notifier
            )
            if diff:
                diff_results.append(diff)

        # 집계
        total = len(results)
        success = sum(1 for r in results if r.status == "SUCCESS")
        fail = total - success
        diff_count = len(diff_results)

        db.finish_run(run_id, total, success, fail)

        summary = (
            f"백업 완료 | 전체: {total}  성공: {success}  실패: {fail}"
            f"  /  Config 변경: {diff_count}건"
        )
        if log_queue:
            log_queue.put(f"[DONE] {summary}")
        else:
            print(f"\n  {summary}")  # CLI 모드
        logger.info(summary)

        # 전체 완료 요약 알림
        notifier.send_summary(total, success, fail, diff_count)

    finally:
        db.close()

    logger.info(f"===== NBT 백업 종료 | run_id={run_id} =====")


def _print_dry_run(tasks: list[DeviceTask], config: AppConfig) -> None:
    """dry-run 모드: 설정 검증 결과와 장비 목록을 출력합니다."""
    print("\n  [DRY-RUN] 설정 검증 완료 — 실제 장비 접속은 수행하지 않습니다.")
    print(f"\n  장비 목록 (총 {len(tasks)}대):")
    print(f"  {'그룹':<10} {'device_type':<20} {'host'}")
    print(f"  {'-'*50}")
    for task in tasks:
        print(f"  [{task.group_name:<8}] {task.device_type:<20} {task.host}")
    print(f"\n  backup 설정: {config.backup}")
    notify_cfg = config.notify
    slack_on = notify_cfg.get("slack", {}).get("enabled", False)
    email_on = notify_cfg.get("email", {}).get("enabled", False)
    print(f"  알림: Slack={'ON' if slack_on else 'OFF'}  Email={'ON' if email_on else 'OFF'}")
    print()