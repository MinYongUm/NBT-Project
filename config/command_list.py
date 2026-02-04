# 장비별 백업 명령어 목록

# =================================================================
# MGMT 장비 명령어 (IOS-XE)
# =================================================================
MGMT_COMMANDS_TO_RUN = [
    'terminal length 0',
    'show version',
    'show int status',
    'show int count errors',
    'show ip int brief',
    'show ip route',
    'show int trunk',
    'show int transceiver detail',
    'show spanning-tree su',
    'show spanning-tree root',
    'show spanning-tree',
    'show vlan brief',
    'show module',
    'show ntp status',
    'show boot',
    'show running-config',
    'show log',
    'terminal length 49'
]

# =================================================================
# NEXUS 장비 명령어 (NX-OS)
# =================================================================
NEXUS_COMMANDS_TO_RUN = [
    'terminal length 0',
    'show version',
    'show int status',
    'show int count errors',
    'show ip int brief vrf all',
    'show ip route vrf all',
    'show int trunk',
    'show int transceiver detail',
    'show port-channel su',
    'show spanning-tree su',
    'show spanning-tree root',
    'show spanning-tree',
    'show vlan brief',
    'show hsrp brief',
    'show system resources',
    'show env',
    'show core',
    'show module',
    'show ntp peers',
    'show ntp status',
    'show boot',
    'show running-config',
    'show logging log',
    'terminal length 49'
]


# =================================================================
# ACI 장비 명령어 (Spine/Leaf)
# =================================================================
ACI_COMMANDS_TO_RUN = [
    'show int status',
    'show version',
    'show int count errors',
    'show ip int brief vrf all',
    'show ip route vrf all',
    'show int trunk',
    'show int transceiver detail',
    'show port-channel su',
    'show mcp internal info vlan all',
    'show vlan brief',
    'show hsrp brief',
    'show system resources',
    'show env',
    'show core',
    'show module',
    'show ntp peers',
    'show lldp neighbors',
    'show cdp neighbors '
]