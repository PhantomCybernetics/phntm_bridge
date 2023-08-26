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

# one publisher for all peers
# this can indeed lead for compeition
class TopicWritePublisher:

    def __init__(self, node:Node, topic:str, protocol:str, cbg:CallbackGroup, log_message_every_sec:float):
        self.pub:Publisher = None
        self.node:Node = node
        self.callback_group:CallbackGroup=cbg
        self.peers:list[str] = []
        self.topic:str = topic
        self.protocol:str = protocol

        self.num_written:int = 0
        self.last_msg:any = None
        self.last_received_time:float = -1.0
        self.last_time_logged:float = -1.0
        self.log_message_every_sec:float = log_message_every_sec

    def start(self, id_peer:str) -> bool:

        if self.pub != None:
            if not id_peer in self.peers:
                self.peers.append(id_peer)
            return True #all done, one pub for all

        message_class = None
        try:
            message_class = get_message(self.protocol)
        except:
            pass

        if message_class == None:
            self.node.get_logger().error(f'NOT creating publisher for topic {self.topic}, msg class {self.protocol} not loaded')
            return False

        # reliable from here
        qos = QoSProfile(history=QoSHistoryPolicy.KEEP_LAST, \
                         depth=1, \
                         reliability=QoSReliabilityPolicy.BEST_EFFORT \
                         )

        self.pub = self.node.create_publisher(message_class, self.topic, qos, callback_group=self.callback_group)
        if self.pub == None:
            self.get_logger().error(f'Failed creating publisher for topic {self.topic}, protocol={self.protocol}, peer={id_peer}')
            return False

        if not id_peer in self.peers:
            self.peers.append(id_peer)

        return True

    def publish(self, id_peer:str, msg:any):

        self.num_written += 1
        self.last_msg = msg
        self.last_received_time = time.time()

        if time.time()-self.last_time_logged > self.log_message_every_sec:
            self.last_time_logged = time.time() #logged now
            if type(msg) is bytes:
                self.node.get_logger().info(f'▼ {self.topic} got message: {len(msg)}B from id_peer={id_peer}, total rcvd: {self.num_written}')
            else:
                self.node.get_logger().info(f'▼ {self.topic} got message: {len(msg)}, from id_peer={id_peer}, total rcvd: {self.num_written}')

        self.pub.publish(msg);

    def stop(self, id_peer:str) -> bool:
        if id_peer in self.peers:
            self.peers.remove(id_peer)

        if self.peers.count == 0:
            self.node.get_logger().info(f'Destroying local publisher for {self.topic}')

            self.pub.destroy()
            self.pub = None
            self.topic = None

            return True #destroyed
        else:
            return False