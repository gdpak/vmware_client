import re

def parse_vmknic(port_key, cmd_out):
  VMKNIC_TABLE_ROW_RE = re.compile(r"^(vmk\d+)\s+(.*\S+)\s*IPv[46]\s+"
                                   r"(\d+\.\d+\.\d+\.\d+)\s+"
                                   r"(\d+\.\d+\.\d+\.\d+).*(true|false)"
                                   r"\s+(\w+)")
  vmknic_list = cmd_out.strip().split("\n")[1:-1]
  for device_config_line in vmknic_list:
    match = VMKNIC_TABLE_ROW_RE.search(device_config_line)
    if not match:
      continue
    print (match.group(1), match.group(2))

cmd_out='''

ot@omega-1:~] esxcfg-vmknic -l
Interface  Port Group/DVPort/Opaque Network        IP Family IP Address                              Netmask         Broadcast       MAC Address       MTU     TSO MSS   Enabled Type                NetStack
vmk0       2                                       IPv4      10.47.242.69                            255.255.240.0   10.47.255.255   00:25:90:dd:e4:04 1500    65535     true    STATIC              defaultTcpipStack
vmk0       2                                       IPv6      fe80::225:90ff:fedd:e404                64                              00:25:90:dd:e4:04 1500    65535     true    STATIC, PREFERRED   defaultTcpipStack
vmk1       ntnx-internal-vmk                       IPv4      192.168.5.1                             255.255.255.0   192.168.5.255   00:50:56:6e:8f:b3 1500    65535     true    STATIC              defaultTcpipStack
vmk1       ntnx-internal-vmk                       IPv6      fe80::250:56ff:fe6e:8fb3                64                              00:50:56:6e:8f:b3 1500    65535     true    STATIC, PREFERRED   defaultTcpipStack
vmk2       32                                      IPv4      172.17.0.1                              255.255.0.0     172.17.255.255  00:50:56:6f:ed:28 1500    65535     true    STATIC              defaultTcpipStack
vmk2       32                                      IPv6      fe80::250:56ff:fe6f:ed28                64                              00:50:56:6f:ed:28 1500    65535     true    STATIC, PREFERRED   defaultTcpipStack
vmk3       e647f7a5-60f6-4e15-b796-8019ec499d9d    IPv4      172.16.8.1                              255.255.0.0     172.16.255.255  00:50:56:65:cc:82 1500    65535     true    STATIC              defaultTcpipStack
vmk3       e647f7a5-60f6-4e15-b796-8019ec499d9d    IPv6      fe80::250:56ff:fe65:cc82                64                              00:50:56:65:cc:82 1500    65535     true    STATIC, PREFERRED   defaultTcpipStack
'''
parse_vmknic('e647f7a5-60f6-4e15-b796-8019ec499d9d', cmd_out) 
