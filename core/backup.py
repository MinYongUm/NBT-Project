"""
NBT (Network Backup Tools) - Main Backup Module
Version: 3.0

Cisco IOS, IOS-XE, NX-OS 장비의 설정을 자동으로 백업하고
직전 백업과의 Config Diff를 감지하여 DB에 저장합니다.
동적 상태값(uptime, 트래픽 카운터 등) 라인은 비교 시 제외하여 오탐을 방지합니다.

Update History:
- ver 1.0 (2025/09/15): Netmiko 마이그레이션, 다중 장비 그룹 지원
- ver 1.1 (2025/09/15): logging 추가, 재시도 5회
- ver 1.2 (2025/09/22): session_timeout 30초
- ver 2.0: Docker 환경 적응, pathlib 전환, 함수 기반 구조
- ver 2.1: YAML 설정 전환, config_loader 도입, 예외 타입 세분화
- ver 2.2: SQLite DB 연동 (DBManager), BackupResult 반환
- ver 2.3: ThreadPoolExecutor 병렬 실행, DeviceTask/BackupResult dataclass
- ver 3.0: Config Diff 감지 (difflib) + 노이즈 라인 필터링
"""

import difflib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from netmiko import ConnectHandler
from netmiko.exceptions import (
    NetmikoAuthenticationException,
    NetmikoTimeoutException,
)

from utils.config_loader import load_config
from utils.db_manager import DBManager
from utils.folder_create import create_backup_folder, create_log_folder

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 데이터 클래스
# ------------------------------------------------------------------

@dataclass
class DeviceTask:
    """단일 장비 백업 작업 정보."""
    host: str
    device_type: str
    os_type: str
    commands: list[str]


@dataclass
class BackupResult:
    """단일 장비 백업 결과."""
    host: str
    hostname: str = ""
    status: str = "FAIL"
    file_path: str = ""
    duration_sec: float = 0.0
    error_msg: str = ""


@dataclass
class DiffResult:
    """단일 장비 Config Diff 결과."""
    hostname: str
    ip: str
    previous_file: str
    current_file: str
    diff_lines: int
    diff_content: str
    has_diff: bool


# ------------------------------------------------------------------
# 로깅 설정
# ------------------------------------------------------------------

def _setup_logging(log_folder: Path) -> None:
    """로그 폴더에 파일 핸들러를 설정합니다."""
    log_file = log_folder / "nbt_backup.log"
    logging.basicConfig(
        filename=str(log_file),
        level=logging.INFO,
        format="%(asctime)s - [%(threadName)s] - %(levelname)s - %(message)s",
        encoding="utf-8",
    )


# ------------------------------------------------------------------
# 장비 태스크 빌드
# ------------------------------------------------------------------

def _build_device_tasks(config) -> list[DeviceTask]:
    """
    config.devices를 순회하여 DeviceTask 리스트로 평탄화합니다.

    Returns:
        전체 장비 DeviceTask 리스트.
    """
    tasks: list[DeviceTask] = []
    for group_name, group_info in config.devices.items():
        device_type: str = group_info["device_type"]
        hosts: list[str] = group_info.get("hosts", [])
        commands: list[str] = config.commands.get(group_name, [])

        for host in hosts:
            tasks.append(DeviceTask(
                host=host,
                device_type=device_type,
                os_type=group_name.upper(),
                commands=commands,
            ))
    return tasks


# ------------------------------------------------------------------
# 노이즈 필터링
# ------------------------------------------------------------------

def _filter_noise(lines: list[str], noise_patterns: list[str]) -> list[str]:
    """
    노이즈 패턴이 포함된 라인을 제거합니다 (대소문자 구분 없음).

    비교 연산에만 사용하며, 백업 파일 원본에는 적용하지 않습니다.

    Args:
        lines: 비교 대상 라인 리스트.
        noise_patterns: 제외할 패턴 문자열 리스트.

    Returns:
        노이즈 라인이 제거된 라인 리스트.
    """
    if not noise_patterns:
        return lines

    # 패턴 목록을 한 번만 컴파일 (대소문자 무시)
    compiled = [re.compile(re.escape(p), re.IGNORECASE) for p in noise_patterns]

    return [
        line for line in lines
        if not any(pattern.search(line) for pattern in compiled)
    ]


# ------------------------------------------------------------------
# Config Diff
# ------------------------------------------------------------------

def _run_diff(
    db: DBManager,
    run_id: int,
    result: BackupResult,
    task: DeviceTask,
    noise_patterns: list[str],
) -> DiffResult | None:
    """
    직전 백업 파일과 현재 백업 파일을 비교하여 DiffResult를 반환합니다.

    비교 흐름:
        1. DB에서 이전 성공 백업 파일 경로 조회
        2. 이전 파일 없음 → None 반환 (첫 백업)
        3. 양쪽 파일에서 노이즈 라인 제거 후 unified_diff 실행
        4. 변경 없음 → has_diff=False
        5. 변경 있음 → DB 저장 + has_diff=True

    백업 파일 원본은 수정하지 않습니다.

    Args:
        db: DBManager 인스턴스.
        run_id: 현재 실행 run_id.
        result: 현재 장비 BackupResult.
        task: 현재 장비 DeviceTask.
        noise_patterns: 비교 시 제외할 패턴 목록.

    Returns:
        DiffResult 또는 None (이전 백업 없음).
    """
    previous_path_str = db.get_last_backup_path(result.hostname, run_id)

    if previous_path_str is None:
        logger.info("[%s] 이전 백업 없음 — 첫 백업으로 처리, Diff 생략", result.hostname)
        return None

    previous_path = Path(previous_path_str)
    current_path = Path(result.file_path)

    if not previous_path.exists():
        logger.warning(
            "[%s] 이전 백업 파일이 존재하지 않음: %s — Diff 생략",
            result.hostname, previous_path,
        )
        return None

    # 원본 라인 로드
    previous_lines_raw = previous_path.read_text(encoding="utf-8").splitlines(keepends=True)
    current_lines_raw = current_path.read_text(encoding="utf-8").splitlines(keepends=True)

    # 노이즈 라인 제거 (비교 전용 — 원본 파일 무변경)
    previous_lines = _filter_noise(previous_lines_raw, noise_patterns)
    current_lines = _filter_noise(current_lines_raw, noise_patterns)

    logger.debug(
        "[%s] 필터링 전/후 라인 수 — 이전: %d->%d, 현재: %d->%d",
        result.hostname,
        len(previous_lines_raw), len(previous_lines),
        len(current_lines_raw), len(current_lines),
    )

    # unified_diff 실행
    diff = list(difflib.unified_diff(
        previous_lines,
        current_lines,
        fromfile=str(previous_path),
        tofile=str(current_path),
        lineterm="",
    ))

    # +/- 라인만 카운트 (헤더 라인 +++ / --- 제외)
    changed_lines = sum(
        1 for line in diff
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )
    diff_content = "".join(diff)
    has_diff = changed_lines > 0

    diff_result = DiffResult(
        hostname=result.hostname,
        ip=task.host,
        previous_file=previous_path_str,
        current_file=result.file_path,
        diff_lines=changed_lines,
        diff_content=diff_content,
        has_diff=has_diff,
    )

    if has_diff:
        db.save_diff(
            run_id=run_id,
            hostname=result.hostname,
            ip=task.host,
            previous_file=previous_path_str,
            current_file=result.file_path,
            diff_lines=changed_lines,
            diff_content=diff_content,
        )
        logger.warning(
            "[%s] Config 변경 감지 — 변경 라인: %d", result.hostname, changed_lines
        )
        print(f"  [DIFF] {result.hostname}: 변경 감지 ({changed_lines}라인)")
    else:
        logger.info("[%s] Config 변경 없음", result.hostname)
        print(f"  [OK]   {result.hostname}: 변경 없음")

    return diff_result


# ------------------------------------------------------------------
# 개별 장비 백업
# ------------------------------------------------------------------

def _backup_device(
    task: DeviceTask,
    backup_folder: Path,
    username: str,
    password: str,
    max_retries: int,
    retry_delay: int,
    session_timeout: int,
) -> BackupResult:
    """
    단일 장비에 SSH 접속하여 명령어를 실행하고 백업 파일을 저장합니다.

    Args:
        task: 백업 대상 장비 정보.
        backup_folder: 백업 파일 저장 폴더.
        username: SSH 접속 계정.
        password: SSH 접속 비밀번호.
        max_retries: 최대 재시도 횟수.
        retry_delay: 재시도 대기 시간(초).
        session_timeout: SSH 세션 타임아웃(초).

    Returns:
        BackupResult (status: 'SUCCESS' or 'FAIL').
    """
    result = BackupResult(host=task.host)
    start_time = time.monotonic()

    device_params = {
        "device_type": task.device_type,
        "host": task.host,
        "username": username,
        "password": password,
        "session_timeout": session_timeout,
    }

    for attempt in range(max_retries):
        try:
            print(f"  [{task.host}] 백업 시작 (시도 {attempt + 1}/{max_retries})")
            logger.info("백업 시작: %s (시도 %d/%d)", task.host, attempt + 1, max_retries)

            with ConnectHandler(**device_params) as net_connect:
                hostname = net_connect.find_prompt().strip("#>")
                result.hostname = hostname
                print(f"  [{task.host} >> {hostname}] 연결 성공")
                logger.info("[%s >> %s] 연결 성공", task.host, hostname)

                full_output = ""
                for command in task.commands:
                    logger.info("[%s >> %s] 명령어 실행: %s", task.host, hostname, command)
                    output = net_connect.send_command(command)
                    full_output += f"\n\n{'=' * 10} {command} {'=' * 10}\n\n{output}"

                file_path = backup_folder / f"{hostname}.txt"
                file_path.write_text(full_output.strip(), encoding="utf-8")

                result.status = "SUCCESS"
                result.file_path = str(file_path)
                result.duration_sec = round(time.monotonic() - start_time, 2)

                print(f"  [{task.host} >> {hostname}] 백업 완료: {file_path}")
                logger.info("[%s >> %s] 백업 파일 저장 성공: %s", task.host, hostname, file_path)
                break

        except NetmikoAuthenticationException as e:
            result.error_msg = f"인증 실패: {e}"
            logger.error("[%s] 인증 실패 — 재시도 없이 중단: %s", task.host, e)
            print(f"  [{task.host}] 인증 실패 — 재시도 없이 중단")
            break

        except NetmikoTimeoutException as e:
            result.error_msg = f"타임아웃: {e}"
            logger.warning("[%s] 타임아웃 (시도 %d/%d): %s", task.host, attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                print(f"  [{task.host}] 타임아웃 — {retry_delay}초 후 재시도")
                time.sleep(retry_delay)
            else:
                logger.error("[%s] 최종 연결 실패 (타임아웃)", task.host)
                print(f"  [{task.host}] 최종 연결 실패")

        except Exception as e:
            result.error_msg = str(e)
            logger.error("[%s] 오류 (시도 %d/%d): %s", task.host, attempt + 1, max_retries, e, exc_info=True)
            if attempt < max_retries - 1:
                print(f"  [{task.host}] 오류 — {retry_delay}초 후 재시도")
                time.sleep(retry_delay)
            else:
                logger.error("[%s] 최종 연결 실패", task.host)
                print(f"  [{task.host}] 최종 연결 실패")

    if result.duration_sec == 0.0:
        result.duration_sec = round(time.monotonic() - start_time, 2)

    return result


# ------------------------------------------------------------------
# 메인 진입점
# ------------------------------------------------------------------

def run_backup() -> None:
    """전체 백업 및 Config Diff 작업을 실행합니다."""
    config = load_config()

    backup_folder = create_backup_folder()
    log_folder = create_log_folder()
    _setup_logging(log_folder)

    db_path = backup_folder.parent / "nbt_history.db"
    db = DBManager(db_path)
    db.initialize()

    run_id = db.start_run()
    logger.info("백업 저장 경로: %s", backup_folder)

    tasks = _build_device_tasks(config)
    total = len(tasks)
    results: list[tuple[DeviceTask, BackupResult]] = []

    max_workers: int = config.backup.get("max_workers", 4)
    max_retries: int = config.backup.get("max_retries", 5)
    retry_delay: int = config.backup.get("retry_delay", 10)
    session_timeout: int = config.backup.get("session_timeout", 30)
    noise_patterns: list[str] = config.diff.get("noise_patterns", [])

    print(f"\n  장비 수: {total}  /  workers: {max_workers}\n")
    logger.info("병렬 백업 시작 | 장비 수: %d, max_workers: %d", total, max_workers)

    # ---- 병렬 백업 ----
    future_to_task: dict = {}
    with ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="nbt-worker",
    ) as executor:
        for task in tasks:
            future = executor.submit(
                _backup_device,
                task,
                backup_folder,
                config.username,
                config.password,
                max_retries,
                retry_delay,
                session_timeout,
            )
            future_to_task[future] = task

        for future in as_completed(future_to_task):
            task = future_to_task[future]
            try:
                result = future.result()
            except Exception as e:
                logger.error("[%s] Future 예외: %s", task.host, e, exc_info=True)
                result = BackupResult(host=task.host, error_msg=str(e))

            results.append((task, result))
            db.save_result(
                run_id=run_id,
                hostname=result.hostname or task.host,
                ip=task.host,
                device_type=task.device_type,
                os_type=task.os_type,
                status=result.status,
                file_path=result.file_path,
                duration_sec=result.duration_sec,
                error_msg=result.error_msg,
            )

    # ---- Config Diff ----
    print("\n  ---- Config Diff 분석 ----")
    logger.info("===== Config Diff 분석 시작 =====")

    diff_count = 0
    for task, result in results:
        if result.status != "SUCCESS":
            continue
        diff_result = _run_diff(db, run_id, result, task, noise_patterns)
        if diff_result and diff_result.has_diff:
            diff_count += 1

    logger.info("Config Diff 분석 완료 | 변경 감지: %d건", diff_count)

    # ---- 집계 ----
    success = sum(1 for _, r in results if r.status == "SUCCESS")
    fail = total - success

    try:
        db.finish_run(run_id, total, success, fail)
    finally:
        db.close()

    summary = (
        f"\n  백업 완료 | 전체: {total}  성공: {success}  실패: {fail}"
        f"  /  Config 변경: {diff_count}건"
    )
    print(summary)
    logger.info(summary.strip())


if __name__ == "__main__":
    run_backup()