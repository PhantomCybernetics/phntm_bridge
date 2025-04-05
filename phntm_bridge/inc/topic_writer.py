import asyncio

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel, RTCConfiguration, RTCIceServer

from rclpy.node import Node, Parameter, Subscription, QoSProfile, Publisher
from rclpy.duration import Duration, Infinite
from rclpy.impl.rcutils_logger import RcutilsLogger
from rosidl_runtime_py.utilities import get_message
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy

from typing import Callable
import time
import concurrent.futures

from rclpy.serialization import deserialize_message
from std_msgs.msg import Header
from termcolor import colored as c

import threading

# one publisher for all peers
# this can indeed lead for compeition
class TopicWritePublisher:

    def __init__(self, node:Node, topic:str, protocol:str, qos:QoSProfile, log_message_every_sec:float):
        self.pub:Publisher = None
        self.node:Node = node
        self.peers:list[str] = []
        self.topic:str = topic
        self.protocol:str = protocol
        self.qos:QoSProfile = qos
        
        self.num_received:int = 0
        self.num_written:int = 0
        self.last_msg:any = None
        self.last_received_time:float = -1.0
        self.last_time_logged:float = -1.0
        self.log_message_every_sec:float = log_message_every_sec

        self.write_pool = concurrent.futures.ThreadPoolExecutor()
        self.last_publish_future:asyncio.Future = None
        self.last_msg:any = None
        self.last_received_msg_stamp:float = -1.0

        self.message_class = None
        try:
            self.message_class = get_message(self.protocol)
        except:
            pass
        if self.message_class == None:
            self.node.get_logger().error(f'NOT creating publisher for topic {self.topic}, msg class {self.protocol} not loaded')
            return False

    def start(self, id_peer:str) -> bool:

        if self.pub != None:
            if not id_peer in self.peers:
                self.peers.append(id_peer)
            return True #all done, one pub for all

        self.pub = self.node.create_publisher(self.message_class, self.topic, self.qos)
        if self.pub == None:
            self.get_logger().error(f'Failed creating publisher for topic {self.topic}, protocol={self.protocol}, peer={id_peer}')
            return False

        if not id_peer in self.peers:
            self.peers.append(id_peer)

        return True

    def publish(self, id_peer:str, msg:any):
        
        drop=False
        msg_header:Header = None

        self.num_received += 1
        
        if hasattr(self.message_class, 'header'):
            msg_header = deserialize_message(msg, Header)
            msg_s:float = msg_header.stamp.sec + (msg_header.stamp.nanosec * 1.0/1000000000.0)
            msg_delta_s:float = (msg_s - self.last_received_msg_stamp) if (self.last_received_msg_stamp > -1.0) else 0.0
            
            self.last_received_msg_stamp = msg_s
            expected_delta_s = msg_delta_s
        else:
            expected_delta_s = 0.033 #temp 30 Hz expected

        self.last_msg = msg #save last always

        now = time.time()
        local_delta_s:float = (now - self.last_received_time) if (self.last_received_time > -1.0) else 0.0
        self.last_received_time = now

        color:str = None
        if local_delta_s < expected_delta_s/4.0: #local buff burst
            color = 'red'
            drop = True
        if local_delta_s > expected_delta_s*2.0: #delayed in transport
            color = 'cyan'
            drop = False
        if msg_header and expected_delta_s > 1.0: # after pause
            color = 'magenta'
            drop = False  
        # if drop:
        #     if msg_header:
        #         print (c(f'{self.topic} DROP msg: {str(msg_header.stamp.sec)}:{str(msg_header.stamp.nanosec)}, delta msg={msg_delta_s} local={local_delta_s} s', color))
        #     else:
        #         print (c(f'{self.topic} DROP msg: exp delta msg={expected_delta_s} local={local_delta_s} s', color))
        #     return
        # else:
        #     print (c(f'{self.topic} msg: local delta={local_delta_s} s', color))

        if time.time()-self.last_time_logged > self.log_message_every_sec:
            self.last_time_logged = time.time() #logged now
            if type(msg) is bytes:
                self.node.get_logger().info(f'🐵 {self.topic} got message: {len(msg)}B from id_peer={id_peer}, total rcvd: {self.num_received}, written={self.num_written}')
            else:
                self.node.get_logger().info(f'🐵 {self.topic} got message: {len(msg)}, from id_peer={id_peer}, total rcvd: {self.num_received}, written={self.num_written}')
        
        self.num_written += 1
        self.pub.publish(self.last_msg)
        # self.pub.publish(self.last_msg)

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