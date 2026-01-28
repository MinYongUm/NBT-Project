"""
NBT (Network Backup Tools)
Cisco 네트워크 장비 자동 백업 도구

사용법:
    python main.py

지원 장비:
    - Cisco IOS / IOS-XE (MGMT)
    - Cisco NX-OS (Nexus)
    - Cisco ACI (Spine/Leaf)
"""

from core.backup import run_backup

if __name__ == "__main__":
    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║     NBT (Network Backup Tools) v1.2                       ║
    ║     Cisco Network Device Backup Automation                ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
    """)
    run_backup()
