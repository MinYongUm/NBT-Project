"""
NBT (Network Backup Tools) - Legacy Version 1.0

이 파일은 이전 개발 버전으로, 참고용으로 보관합니다.
현재는 core/backup.py를 사용하세요.

Update 내역:
- ver 1.0 (2025/09/15)
  - Netmiko 라이브러리로 마이그레이션 (paramiko 삭제)
  - device_list.py, command_list.py 순회 반복문 추가 (Cisco OS별 command 상이)
"""

from netmiko import ConnectHandler
import os
import time
from config import account_info, command_list, device_list, folder_create

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
# 이중 반복문: 작업 목록 순회 -> 장비 목록 순회
# =================================================================
for task in TASKS:
    print(f"\n############################################################")
    print(f"## 시작: {task['group_name']} 장비 그룹 백업")
    print(f"############################################################\n")
    
    DEVICES = task['devices']
    COMMANDS_TO_RUN = task['commands']
    DEVICE_TYPE = task['device_type']

    for device in DEVICES:
        host = device['host']

        device_params = {
            'device_type': DEVICE_TYPE, 
            'host': host,
            'username': USERNAME,
            'password': PASSWORD,
            'session_timeout': 20,
        }

        MAX_RETRIES = 3
        RETRY_DELAY = 10

        for attempt in range(MAX_RETRIES):
            try:
                print(f"=============== 백업 시작: {host} (시도 {attempt + 1}/{MAX_RETRIES}) ===============")

                with ConnectHandler(**device_params) as net_connect:
                    hostname = net_connect.find_prompt().strip('#>')
                    print(f"[{host} >> {hostname}] 연결 성공!!")

                    file_name = f"{hostname}.txt"
                    file_path = os.path.join(FULL_FOLDER_PATH, file_name)
                    print(f"[{host} >> {hostname}] 로그 파일 생성 경로: {file_path}")

                    full_output = ""
                
                    for command in COMMANDS_TO_RUN:
                        print(f"[{host} >> {hostname}] 명령어 실행: {command}")
                        output = net_connect.send_command(command)
                        full_output += f"\n\n{'='*10} {command} {'='*10}\n\n{output}"

                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(full_output.strip())

                    print(f"[{host} >> {hostname}] 모든 로그가 '{file_path}' 파일로 저장되었습니다.")
                    break

            except Exception as e:
                print(f"[{host}] 오류 발생: {e}")
                if attempt < MAX_RETRIES - 1:
                    print(f"[{host}] {RETRY_DELAY}초 후 재시도합니다...")
                    time.sleep(RETRY_DELAY)
                else:
                    print(f"[{host}] 최종 연결에 실패했습니다.")
        
        print(f"[{host}] 작업 완료 및 연결 종료..\n")
