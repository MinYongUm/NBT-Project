"""
NBT (Network Backup Tools) - Main Backup Module
Version: 2.2

Cisco IOS, IOS-XE, NX-OS 장비의 설정을 자동으로 백업하는 도구.

Update History:
- ver 1.0 (2025/09/15): Netmiko 마이그레이션, 다중 장비 그룹 지원
- ver 1.1 (2025/09/15): logging 추가, 재시도 5회
- ver 1.2 (2025/09/22): session_timeout 30초
- ver 2.0 (2026/03/09): Docker 환경 적응, pathlib 전환, 함수 기반 구조
- ver 2.1 (2026/03/10): YAML 설정 전환, config_loader 도입, 예외 세분화
- ver 2.2 (2026/03/10): SQLite DB 연동, 백업 이력 저장
"""

import time
import logging
from pathlib import Path

from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoAuthenticationException, NetmikoTimeoutException

from utils.config_loader import load_config, AppConfig
from utils.folder_create import create_backup_folder, create_log_folder
from utils.db_manager import DBManager

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 로깅 설정
# ------------------------------------------------------------------

def setup_logging(log_folder: Path) -> None:
    """로그 폴더에 파일 로깅을 설정합니다.

    Args:
        log_folder: 로그 파일을 저장할 폴더 경로
    """
    log_file = log_folder / 'nbt_backup.log'
    logging.basicConfig(
        filename=str(log_file),
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        encoding='utf-8'
    )


# ------------------------------------------------------------------
# 개별 장비 백업
# ------------------------------------------------------------------

def backup_device(
    host:          str,
    device_type:   str,
    os_type:       str,
    commands:      list[str],
    backup_folder: Path,
    username:      str,
    password:      str,
    db:            DBManager,
    run_id:        int,
    max_retries:   int = 5,
    retry_delay:   int = 10,
) -> str:
    """개별 장비 1대를 백업하고 결과를 DB에 저장합니다.

    Args:
        host:          장비 IP 주소
        device_type:   Netmiko device_type (예: 'cisco_ios')
        os_type:       장비 그룹명 (예: 'MGMT', 'NEXUS', 'ACI')
        commands:      실행할 명령어 목록
        backup_folder: 백업 파일 저장 폴더
        username:      SSH 접속 계정
        password:      SSH 접속 비밀번호
        db:            DBManager 인스턴스
        run_id:        현재 실행의 run_id
        max_retries:   최대 재시도 횟수 (settings.yaml에서 수신)
        retry_delay:   재시도 대기 시간(초) (settings.yaml에서 수신)

    Returns:
        str: 'SUCCESS' 또는 'FAIL'
    """
    device_params = {
        'device_type':     device_type,
        'host':            host,
        'username':        username,
        'password':        password,
        'session_timeout': 30,
    }

    for attempt in range(max_retries):
        start_time = time.time()
        print(f"=============== 백업 시작: {host} (시도 {attempt + 1}/{max_retries}) ===============")
        logger.info(f"백업 시작: {host} (시도 {attempt + 1}/{max_retries})")

        try:
            with ConnectHandler(**device_params) as net_connect:
                hostname     = net_connect.find_prompt().strip('#>')
                duration_sec = round(time.time() - start_time, 2)

                print(f"[{host} >> {hostname}] 연결 성공!!")
                logger.info(f"[{host} >> {hostname}] 연결 성공.")

                file_name = f"{hostname}.txt"
                file_path = backup_folder / file_name
                print(f"[{host} >> {hostname}] 백업 파일 경로: {file_path}")

                full_output = ""
                for command in commands:
                    print(f"[{host} >> {hostname}] 명령어 실행: {command}")
                    output = net_connect.send_command(command)
                    full_output += f"\n\n{'='*10} {command} {'='*10}\n\n{output}"

                file_path.write_text(full_output.strip(), encoding='utf-8')
                print(f"[{host} >> {hostname}] 백업 완료: {file_path}")
                logger.info(f"[{host} >> {hostname}] 백업 파일 저장 성공: {file_path}")

                db.save_result(
                    run_id=run_id,
                    hostname=hostname,
                    ip=host,
                    device_type=device_type,
                    os_type=os_type,
                    status='SUCCESS',
                    file_path=str(file_path),
                    duration_sec=duration_sec,
                )
                print(f"[{host}] 작업 완료 및 연결 종료.\n")
                return 'SUCCESS'

        except NetmikoAuthenticationException as e:
            # 인증 실패는 재시도해도 의미 없음 → 즉시 중단
            duration_sec = round(time.time() - start_time, 2)
            error_msg    = f"인증 실패: {e}"
            print(f"[{host}] {error_msg}")
            logger.error(f"[{host}] {error_msg}")

            db.save_result(
                run_id=run_id,
                hostname=None,
                ip=host,
                device_type=device_type,
                os_type=os_type,
                status='FAIL',
                error_msg=error_msg,
                duration_sec=duration_sec,
            )
            print(f"[{host}] 인증 실패로 재시도 없이 종료.\n")
            return 'FAIL'

        except NetmikoTimeoutException as e:
            # 타임아웃은 재시도 가능
            duration_sec = round(time.time() - start_time, 2)
            error_msg    = f"타임아웃: {e}"
            print(f"[{host}] {error_msg}")
            logger.warning(f"[{host}] {error_msg}")

            if attempt < max_retries - 1:
                print(f"[{host}] {retry_delay}초 후 재시도합니다...")
                logger.warning(f"[{host}] {retry_delay}초 후 재시도.")
                time.sleep(retry_delay)
                continue

            db.save_result(
                run_id=run_id,
                hostname=None,
                ip=host,
                device_type=device_type,
                os_type=os_type,
                status='FAIL',
                error_msg=error_msg,
                duration_sec=duration_sec,
            )
            print(f"[{host}] 최종 연결 실패.\n")
            logger.error(f"[{host}] 최종 연결 실패.")
            return 'FAIL'

        except Exception as e:
            # 그 외 예상치 못한 예외
            duration_sec = round(time.time() - start_time, 2)
            error_msg    = f"예외 발생: {type(e).__name__}: {e}"
            print(f"[{host}] {error_msg}")
            logger.error(f"[{host}] {error_msg}", exc_info=True)

            if attempt < max_retries - 1:
                print(f"[{host}] {retry_delay}초 후 재시도합니다...")
                time.sleep(retry_delay)
                continue

            db.save_result(
                run_id=run_id,
                hostname=None,
                ip=host,
                device_type=device_type,
                os_type=os_type,
                status='FAIL',
                error_msg=error_msg,
                duration_sec=duration_sec,
            )
            print(f"[{host}] 최종 연결 실패.\n")
            logger.error(f"[{host}] 최종 연결 실패.")
            return 'FAIL'

    # 루프 정상 종료 불가 경로 (안전장치)
    return 'FAIL'


# ------------------------------------------------------------------
# 전체 백업 실행
# ------------------------------------------------------------------

def run_backup() -> None:
    """전체 백업 작업을 실행합니다.

    순서:
        1. 설정 로드 (YAML + 환경변수)
        2. 폴더 생성 (백업, 로그)
        3. DB 초기화 및 실행 시작 기록
        4. 장비 그룹별 순회 백업
        5. 실행 종료 기록 (집계)
    """
    # 1. 설정 로드
    config: AppConfig = load_config()
    username    = config.username
    password    = config.password
    max_retries = config.backup.get('max_retries', 5)
    retry_delay = config.backup.get('retry_delay', 10)

    # 2. 폴더 생성
    backup_folder = create_backup_folder()
    log_folder    = create_log_folder()
    setup_logging(log_folder)

    logger.info("===== NBT 백업 스크립트 시작 =====")
    logger.info(f"백업 저장 경로: {backup_folder}")

    # 3. DB 초기화
    db_path = backup_folder.parent / 'nbt_history.db'
    db = DBManager(db_path)
    db.initialize()
    run_id = db.start_run()
    logger.info(f"DB 실행 기록 시작. run_id={run_id}")

    # 4. 집계 카운터
    total_count   = 0
    success_count = 0
    fail_count    = 0

    try:
        # config.devices: {'mgmt': {'device_type': ..., 'hosts': [...]}, 'nexus': ..., 'aci': ...}
        # config.commands: {'mgmt': [...], 'nexus': [...], 'aci': [...]}
        for group_name, group_cfg in config.devices.items():
            device_type = group_cfg['device_type']
            hosts       = group_cfg['hosts']
            commands    = config.commands.get(group_name, [])

            print(f"\n############################################################")
            print(f"## 시작: {group_name.upper()} 장비 그룹 백업")
            print(f"############################################################\n")
            logger.info(f"===== 시작: {group_name.upper()} 장비 그룹 백업 =====")

            for host in hosts:
                total_count += 1
                result = backup_device(
                    host=host,
                    device_type=device_type,
                    os_type=group_name.upper(),
                    commands=commands,
                    backup_folder=backup_folder,
                    username=username,
                    password=password,
                    db=db,
                    run_id=run_id,
                    max_retries=max_retries,
                    retry_delay=retry_delay,
                )
                if result == 'SUCCESS':
                    success_count += 1
                else:
                    fail_count += 1

    finally:
        # 예외 발생 여부와 무관하게 반드시 실행
        db.finish_run(
            run_id=run_id,
            total=total_count,
            success=success_count,
            fail=fail_count,
        )
        db.close()

        print(f"\n{'='*60}")
        print(f"  백업 완료 | 전체: {total_count}  성공: {success_count}  실패: {fail_count}")
        print(f"{'='*60}\n")
        logger.info(
            f"===== NBT 백업 스크립트 종료 "
            f"| 전체={total_count} 성공={success_count} 실패={fail_count} ====="
        )


if __name__ == "__main__":
    run_backup()