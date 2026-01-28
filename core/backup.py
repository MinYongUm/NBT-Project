"""
NBT (Network Backup Tools) - Main Backup Module
Version: 1.2

Cisco IOS, IOS-XE, NX-OS 장비의 설정을 자동으로 백업하는 도구입니다.

Update History:
- ver 1.0 (2025/09/15): Netmiko 라이브러리로 마이그레이션, 다중 장비 그룹 지원
- ver 1.1 (2025/09/15): logging 기능 추가, 재시도 횟수 5회로 증가
- ver 1.2 (2025/09/22): session_timeout 30초로 변경
"""

from netmiko import ConnectHandler
import os
import time
import logging
from config import account_info, command_list, device_list
from utils import folder_create

# =================================================================
# 수행할 작업 목록 정의
# =================================================================
TASKS = [
    {
        "group_name": "MGMT",
        "devices": device_list.MGMT_DEVICES,
        "commands": command_list.MGMT_COMMANDS_TO_RUN,
        "device_type": "cisco_ios"
    },
    {
        "group_name": "NEXUS",
        "devices": device_list.NEXUS_DEVICES,
        "commands": command_list.NEXUS_COMMANDS_TO_RUN,
        "device_type": "cisco_nxos"
    },
    {
        "group_name": "ACI",
        "devices": device_list.ACI_DEVICES,
        "commands": command_list.ACI_COMMANDS_TO_RUN,
        "device_type": "cisco_nxos"
    }
]

# =================================================================
# 계정 정보 및 폴더 경로
# =================================================================
USERNAME = account_info.USERNAME
PASSWORD = account_info.PASSWORD
FULL_FOLDER_PATH = folder_create.full_folder_path

# =================================================================
# 로깅 설정
# =================================================================
log_file_path = os.path.join(FULL_FOLDER_PATH, 'nbt_backup.log')

logging.basicConfig(
    filename=log_file_path,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

# =================================================================
# 백업 실행
# =================================================================
def run_backup():
    """전체 백업 작업 실행"""
    logging.info("===== NBT 백업 스크립트 시작 =====")
    
    for task in TASKS:
        group_name = task['group_name']
        devices = task['devices']
        commands = task['commands']
        device_type = task['device_type']

        print(f"\n############################################################")
        print(f"## 시작: {group_name} 장비 그룹 백업")
        print(f"############################################################\n")
        logging.info(f"===== 시작: {group_name} 장비 그룹 백업 =====")
        
        for device in devices:
            backup_device(device['host'], device_type, commands)

    logging.info("===== NBT 백업 스크립트 종료 =====")


def backup_device(host, device_type, commands):
    """개별 장비 백업 수행"""
    device_params = {
        'device_type': device_type,
        'host': host,
        'username': USERNAME,
        'password': PASSWORD,
        'session_timeout': 30,
    }

    MAX_RETRIES = 5
    RETRY_DELAY = 10

    for attempt in range(MAX_RETRIES):
        try:
            print(f"=============== 백업 시작: {host} (시도 {attempt + 1}/{MAX_RETRIES}) ===============")
            logging.info(f"백업 시작: {host} (시도 {attempt + 1}/{MAX_RETRIES})")

            with ConnectHandler(**device_params) as net_connect:
                hostname = net_connect.find_prompt().strip('#>')
                print(f"[{host} >> {hostname}] 연결 성공!!")
                logging.info(f"[{host} >> {hostname}] 연결 성공.")

                file_name = f"{hostname}.txt"
                file_path = os.path.join(FULL_FOLDER_PATH, file_name)
                print(f"[{host} >> {hostname}] 로그 파일 생성 경로: {file_path}")

                full_output = ""
                for command in commands:
                    print(f"[{host} >> {hostname}] 명령어 실행: {command}")
                    output = net_connect.send_command(command)
                    full_output += f"\n\n{'='*10} {command} {'='*10}\n\n{output}"

                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(full_output.strip())

                print(f"[{host} >> {hostname}] 백업 완료: {file_path}")
                logging.info(f"[{host} >> {hostname}] 백업 파일 저장 성공: {file_path}")
                break

        except Exception as e:
            print(f"[{host}] 오류 발생: {e}")
            logging.error(f"[{host}] 백업 실패. 오류: {e}", exc_info=False)

            if attempt < MAX_RETRIES - 1:
                print(f"[{host}] {RETRY_DELAY}초 후 재시도합니다...")
                logging.warning(f"[{host}] {RETRY_DELAY}초 후 재시도합니다...")
                time.sleep(RETRY_DELAY)
            else:
                print(f"[{host}] 최종 연결에 실패했습니다.")
                logging.error(f"[{host}] 최종 연결 실패.")
    
    print(f"[{host}] 작업 완료 및 연결 종료..\n")


if __name__ == "__main__":
    run_backup()
