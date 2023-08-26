import asyncio

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel, RTCConfiguration, RTCIceServer

from rclpy.node import Node, Parameter, Subscription, QoSProfile, Publisher
from rclpy.duration import Duration, Infinite
from rclpy.impl.rcutils_logger import RcutilsLogger
from rosidl_runtime_py.utilities import get_message, get_interface
from rclpy.callback_groups import CallbackGroup
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy

from typing import Callable
import time

import threading




# def key_in_tuple_list(key:str, search_list:list[tuple]):
#     for item in search_list:
#         if item[0] == key:
#             return True
#     return False


# def matches_param_filters(key:str, param:Parameter):
#     for test in param.get_parameter_value().string_array_value:
#         if test == '': continue

#         #if re.search(test, key):
#         #    return True
#         if test == '.*' or test == key[0:len(test)]:
#             return True
#     return False

