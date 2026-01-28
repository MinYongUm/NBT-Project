# 장비별 백업 명령어 목록

# =================================================================
# MGMT 장비 명령어 (IOS-XE)
# =================================================================
MGMT_COMMANDS_TO_RUN = [
    'show running-config',
    'show startup-config',
    'show version',
    'show inventory',
    'show interfaces status',
    'show vlan brief',
    'show etherchannel summary',
    'show ip route',
]

# =================================================================
# NEXUS 장비 명령어 (NX-OS)
# =================================================================
NEXUS_COMMANDS_TO_RUN = [
    'show running-config',
    'show startup-config',
    'show version',
    'show inventory',
    'show interface status',
    'show vlan brief',
    'show vpc',
    'show port-channel summary',
]

# =================================================================
# ACI 장비 명령어 (Spine/Leaf)
# =================================================================
ACI_COMMANDS_TO_RUN = [
    'show running-config',
    'show version',
    'show inventory',
    'show interface status',
    'show lldp neighbors',
]
