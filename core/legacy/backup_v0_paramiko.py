"""
NBT (Network Backup Tools) - Legacy Version 0 (Paramiko)

이 파일은 초기 개발 버전으로, 참고용으로 보관합니다.
현재는 core/backup.py (Netmiko 기반)를 사용하세요.

특징:
- Paramiko 라이브러리 직접 사용
- 단일 장비 그룹만 지원
- 재시도 로직 없음
"""

import paramiko
import time
import re
import os
from datetime import datetime
from config import account_info, command_list, device_list, folder_create

# =================================================================
# 계정 및 접속할 장비 목록 
# =================================================================
DEVICES = device_list.NEXUS_DEVICES
USERNAME = account_info.USERNAME 
PASSWORD = account_info.PASSWORD

# =================================================================
# 실행할 명령어 목록
# =================================================================
COMMANDS_TO_RUN = command_list.NEXUS_COMMANDS_TO_RUN

# =================================================================
# 동적 경로 생성
# =================================================================
FULL_FOLDER_PATH = folder_create.full_folder_path

# =================================================================
# 장비 목록을 순회하며 작업 수행
# =================================================================
for device in DEVICES:
    host = device['host']
    username = USERNAME
    password = PASSWORD

    print(f"=============== 백업 시작: {host} ===============")

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            hostname=host,
            username=username,
            password=password,
            timeout=5,
            look_for_keys=False,
            allow_agent=False
        )

        chan = ssh.invoke_shell()
        time.sleep(2)

        initial_output = chan.recv(65535).decode('utf-8')
        
        prompt_line = initial_output.strip().splitlines()[-1]
        hostname = re.sub(r'[>#\s]+$', '', prompt_line)
        if not hostname:
            hostname = host
       
        print(f"[{host} >> {hostname}] 연결 성공!!")
        
        file_name = f"{hostname}.txt"
        file_path = os.path.join(FULL_FOLDER_PATH, file_name)
        print(f"[{host} >> {hostname}] 로그 파일 생성 경로: {file_path}")

        full_output = ""
        for command in COMMANDS_TO_RUN:
            print(f"[{host} >> {hostname}] 명령어 실행: {command}")
            chan.send(f'{command}\n')
            time.sleep(3)
            output = chan.recv(65535).decode('utf-8')
            full_output += output
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(full_output)

        print(f"[{host} >> {hostname}] 모든 로그가 '{file_path}' 파일로 저장되었습니다.")

    except Exception as e:
        print(f"[{host} >> {hostname}] 오류 발생: {e}")

    finally:
        if 'ssh' in locals() and ssh.get_transport() and ssh.get_transport().is_active():
            ssh.close()
            print(f"[{host} >> {hostname}] 연결 종료..\n")
