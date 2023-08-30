import asyncio
import concurrent.futures

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel, RTCConfiguration, RTCIceServer

from rclpy.node import Node, Parameter, Subscription, QoSProfile, Publisher
from rclpy.duration import Duration, Infinite
from rclpy.impl.rcutils_logger import RcutilsLogger
from rosidl_runtime_py.utilities import get_message, get_interface
from rclpy.callback_groups import CallbackGroup
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy

from termcolor import colored as c

import rclpy

from typing import Callable
import time
from rclpy.callback_groups import CallbackGroup, MutuallyExclusiveCallbackGroup
from rclpy.context import Context
from rclpy.executors import Executor, MultiThreadedExecutor, SingleThreadedExecutor
import threading

import multiprocessing as mp
from queue import Empty, Full

# runs as a separate process for bette isolation and lower ctrl/cam latency
def TopicReadProcessor(running_shared:mp.Value, ctrl_queue:mp.Queue, data_out_queue:mp.Queue,
                        conf:any,
                        log_message_every_sec:float=5.0):

    print('TopicReadProcessor: starting')

    rcl_ctx = Context()
    rcl_ctx.init() # This must be done before any ROS nodes can be created.
    # rcl_cbg = MutuallyExclusiveCallbackGroup()
    rcl_executor = SingleThreadedExecutor(context=rcl_ctx)
    reader_node = Node("phntm_bridge_reader", context=rcl_ctx, enable_rosout=False)

    rcl_executor.add_node(reader_node)
    # rcl_cbg.add_entity(reader_node)
    # rcl_cbg.add_entity(rcl_ctx)
    # rcl_cbg.add_entity(rcl_executor)

    reader_node.get_logger().set_level(rclpy.logging.LoggingSeverity.DEBUG)
    # reader_node.load_config(self.get_logger())

    try:
        while running_shared.value > 0:
            print(c(f'yellow! {reader_node}', 'yellow'))

            while True:
                try:
                    ctrl_cmd = ctrl_queue.get(block=False)
                    reader_node.get_logger().warn(f'Got new CMD: {str(ctrl_cmd)}')
                except Empty:
                    break

            rcl_executor.spin_once(timeout_sec=1.0)
            time.sleep(2)

    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    except Exception as e:
        print(f'Exception in TopicReadProcessor {e}')

    reader_node.destroy_node()
    rcl_executor.shutdown()

    print('TopicReadProcessor: exiting')


class TopicReadSubscription:

    # def __init__(self, sub:Subscription, peers:list[str], frame_processor, processed_frames_h264:mp.Queue, processed_frames_v8:mp.Queue, make_keyframe_shared:mp.Value, make_h264_shared:mp.Value, make_v8_shared:mp.Value):
    def __init__(self, node:Node, topic:str, protocol:str, reliability:QoSReliabilityPolicy, durability:DurabilityPolicy, cbg:CallbackGroup, event_loop:object, log_message_every_sec:float):
        self.sub:Subscription = None
        self.node:Node = node
        self.callback_group:CallbackGroup=cbg
        self.peers:{str:RTCDataChannel} = {} #target outbound dcs
        self.topic:str = topic
        self.protocol:str = protocol

        self.num_received:int = 0
        self.last_msg:any = None
        self.last_msg_time:float = -1.0
        self.last_log:float = -1.0
        self.log_message_every_sec:float = log_message_every_sec

        self.reliability:QoSReliabilityPolicy = reliability
        self.durability:DurabilityPolicy = durability

        self.on_msg_cb:Callable = None
        self.event_loop = event_loop

        #print(f'TopicReadSubscription:__init__() {threading.get_ident()}')

    def start(self, id_peer:str, dc:RTCDataChannel) -> bool:

        if self.sub != None:
            self.peers[id_peer] = dc
            return True #all done, one sub for all

        #print(f'TopicReadSubscription:start() {threading.get_ident()}')
        self.executor = concurrent.futures.ThreadPoolExecutor()

        message_class = None
        try:
            message_class = get_message(self.protocol)
        except:
            pass
        if message_class == None:
            self.node.get_logger().error(f'NOT subscribing to topic {self.topic}, msg class {self.protocol} not loaded')
            return False

        # reliable from here
        qosProfile = QoSProfile(history=QoSHistoryPolicy.KEEP_LAST, \
                                depth=1, \
                                reliability=self.reliability, \
                                durability=self.durability, \
                                lifespan=Infinite \
                                )
        self.node.get_logger().warn(f'Subscribing to topic {self.topic} {self.protocol}')
        self.sub = self.node.create_subscription(
                msg_type=message_class,
                topic=self.topic,
                callback=self.on_msg,
                qos_profile=qosProfile,
                raw=True,
                callback_group=self.callback_group
            )
        if self.sub == None:
            self.node.get_logger().error(f'Failed subscribing to topic {self.topic}, msg class={self.protocol}, peer={id_peer}')
            return False

        self.peers[id_peer] = dc

        return True

    def on_msg(self, msg:any):
        self.num_received += 1
        self.last_msg = msg
        self.last_msg_time = time.time()

        #print(f'TopicReadSubscription:on_msg() {threading.get_ident()}')

        log_msg = False
        if self.num_received == 1: # first data in
            self.node.get_logger().debug(f'Receiving {type(msg).__name__} from {self.topic}')

        if self.last_log < 0 or self.last_msg_time-self.last_log > self.log_message_every_sec:
            log_msg = True
            self.last_log = self.last_msg_time #last logged now

        for id_peer in self.peers.keys():
            dc = self.peers[id_peer]
            if dc.readyState == 'open':
                if log_msg:
                    self.node.get_logger().info(f'△ Sending {len(msg)}B into {self.topic} for id_peer={id_peer}, total received: {self.num_received}')
                # print(f' hello! {self.topic}')
                try:
                    # await self.event_loop.create_task(dc.send(msg)) #always raw bytes bcs fast
                    # self.event_loop.call_soon_threadsafe(dc.send, msg)
                    # def send_this(what):
                        # self.event_loop.run_in_executor(self.executor, dc.send, what)
                    # self.event_loop.call_soon_threadsafe(send_this, msg)
                    self.event_loop.call_soon_threadsafe(dc.send, msg)
                    # await dc.send(msg) #always raw bytes bcs fast
                except Exception as e:
                    print(f'Exception {e}')
            # else:
                # self.node.get_logger().info(c(f'DC {self.topic} for id_peer={id_peer} not open, not sending', 'dark_grey'))
                # self.peers.pop(id_peer)
                # dc.close()
                # self.stop(id_peer)

        # print(f' hello? {self.topic}')

        if self.on_msg_cb is not None:
            self.on_msg_cb()
        else:
            self.node.get_logger().debug(f'on_msg_cb is {self.on_msg_cb}')


    async def report_latest_when_ready(self, id_peer:str):
        while True:
            if not id_peer in self.peers.keys():
                return
            if self.last_msg == None:
                return #nothing received yet, will report when we do
            if self.peers[id_peer].readyState == 'open':
                # asyncio.get_event_loop().create_task(self.peers[id_peer].send())
                self.event_loop.call_soon_threadsafe(self.peers[id_peer].send, self.last_msg)
                return #all done
            else:
                await asyncio.sleep(.5) #wait until dc opens

    def stop(self, id_peer:str) -> bool:
        if id_peer in self.peers.keys():
            self.peers.pop(id_peer)

        if len(self.peers) == 0:
            self.node.get_logger().info(f'Destroying local subscriber for {self.topic}')

            self.node.destroy_subscription(self.sub)
            self.sub = None
            self.topic = None

            return True #destroyed
        else:
            return False