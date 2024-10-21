import asyncio

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel, RTCConfiguration, RTCIceServer

from rclpy.node import Node, Parameter, Subscription, QoSProfile, Publisher
from rclpy.duration import Duration, Infinite
from rclpy.impl.rcutils_logger import RcutilsLogger
from rosidl_runtime_py.utilities import get_message, get_interface
from rclpy.callback_groups import CallbackGroup
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy

import time
import math

def qos_equal(a:QoSProfile, b:QoSProfile) -> bool:
    if not a and b or not b and a:
        return False
    
    if a.depth != b.depth:
        return False
    if a.history != b.history:
        return False
    if a.reliability != b.reliability:
        return False
    if a.durability != b.durability:
        return False
    if a.lifespan != b.lifespan:
        return False
    if a.deadline != b.deadline:
        return False

    return True


def set_message_header(node, msg):
    time_nanosec:int = time.time_ns()
    msg.header.stamp.sec = math.floor(time_nanosec / 1000000000)
    msg.header.stamp.nanosec = time_nanosec % 1000000000
    msg.header.frame_id = node.hostname


def format_bytes(b, mib=False):        
    unit = 1000
    GB = unit * unit * unit # 
    MB = unit * unit # docker stats shows MiB, keep consistent
    KB = unit
    
    if b > GB:
        return f'{(b / GB):.2f}{"GiB" if mib else "GB"}'
    elif b > MB:
        return f'{(b / MB):.2f}{"MiB" if mib else "MB"}'
    elif b > KB:
        return f'{(b / KB):.2f}{"KiB" if mib else "KB"}'
    elif b > 0:
        return f'{(b):.2f}B'
    else:
        return f'0B'