import sys
import os

ROOT = os.path.dirname(__file__)

f = open("/proc/cmdline", "r")
cmd_line = f.read()
args = cmd_line.split(' ')
print(f'KERNEL CMD LINE:\n{cmd_line}')
wifi = None
password = None
for arg in args:
    # print(f'ARG: {arg}')
    if arg.startswith('wifi_connect='):
        wifi = arg.split('=')[1]
    elif arg.startswith('wifi_password:'):
        password = arg.split(':')[1]


if not wifi is None:
    print(f'Setting WIFI: {wifi} PASS: {password}')

# TODO write into /etc/netplan/00-????-wifi_cmdline.yaml
# TODO run netplan apply as root
# TODO add this to boot script