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
import traceback

import multiprocessing as mp
from queue import Empty, Full

class TopicReadSubscription:

    # def __init__(self, sub:Subscription, peers:list[str], frame_processor, processed_frames_h264:mp.Queue, processed_frames_v8:mp.Queue, make_keyframe_shared:mp.Value, make_h264_shared:mp.Value, make_v8_shared:mp.Value):
    def __init__(self, ctrl_node:Node, reader_ctrl_queue:mp.Queue, topic:str, protocol:str, reliability:QoSReliabilityPolicy, durability:DurabilityPolicy, lifespan_sec:int, event_loop:object, log_message_every_sec:float):

        self.sub:Subscription|bool = None
        self.ctrl_node:Node = ctrl_node
        self.reader_ctrl_queue:mp.Queue = reader_ctrl_queue

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
        self.lifespan_sec:int = lifespan_sec

        self.on_msg_cb:Callable = None
        self.event_loop = event_loop
        self.last_send_future:asyncio.Future = None

        # self.send_pool = concurrent.futures.ThreadPoolExecutor()
        #print(f'TopicReadSubscription:__init__() {threading.get_ident()}')

    def start(self, id_peer:str, dc:RTCDataChannel) -> bool:

        if self.sub != None:
            self.peers[id_peer] = dc

            return True #all done, one sub for all

        if self.reader_ctrl_queue: # subscribe on processor's process

            self.pipe_out, self.pipe_worker = mp.Pipe()
            # no_skip:bool = self.protocol in [ 'std_msgs/msg/String', 'rcl_interfaces/msg/Log' ]
            no_skip = True
            self.reader_ctrl_queue.put_nowait({'action': 'subscribe',
                                               'pipe': self.pipe_worker,
                                               'topic': self.topic,
                                               'msg_type': self.protocol,
                                               'reliability': self.reliability,
                                               'durability': self.durability,
                                               'lifespan': self.lifespan_sec,
                                               'no_skip': no_skip
                                               })
            self.sub = True
            
            self.read_task = self.event_loop.create_task(self.read_piped_data())

        else: #subscribe here on ctrl node's process
            #print(f'TopicReadSubscription:start() {threading.get_ident()}')

            message_class = None
            try:
                message_class = get_message(self.protocol)
            except:
                pass
            if message_class == None:
                self.ctrl_node.get_logger().error(f'NOT subscribing to topic {self.topic}, msg class {self.protocol} not loaded')
                return False

            qosProfile = QoSProfile(history=QoSHistoryPolicy.KEEP_LAST, \
                                    depth=1, \
                                    reliability=self.reliability, \
                                    durability=self.durability, \
                                    lifespan=Infinite \
                                    )
            self.ctrl_node.get_logger().warn(f'Subscribing to topic {self.topic} {self.protocol}')
            no_skip:bool = self.protocol in [ 'std_msgs/msg/String', 'rcl_interfaces/msg/Log' ]
            self.sub = self.ctrl_node.create_subscription(
                    msg_type=message_class,
                    topic=self.topic,
                    callback=self.on_msg,
                    qos_profile=qosProfile,
                    raw=True,
                    no_skip=no_skip
                )
            if self.sub == None:
                self.ctrl_node.get_logger().error(f'Failed subscribing to topic {self.topic}, msg class={self.protocol}, peer={id_peer}')
                return False

        self.peers[id_peer] = dc

        return True

    async def read_piped_data(self):
        while True:
            try:
                res = await self.event_loop.run_in_executor(None, self.pipe_out.recv) #blocks
                await self.on_msg(res)

            except (KeyboardInterrupt, asyncio.CancelledError):
                print(f'read_piped_data for {self.topic} got err')
                return
            except Exception as e:
                self.ctrl_node.get_logger().error(f'Exception while reading latest from data pipe: {str(e)}')
                pass

    # called either by ctrl node's process or when data is received via reader_out_queue (called on ctrl node's process)
    async def on_msg(self, reader_res:dict):
        self.num_received += 1
        self.last_msg = reader_res['msg']
        self.last_msg_time = time.time()

        #print(f'TopicReadSubscription:on_msg() {threading.get_ident()}')

        log_msg = False
        if self.num_received == 1 or self.lifespan_sec == -1: # first data in
            self.ctrl_node.get_logger().debug(f'⚡️ Receiving {type(reader_res["msg"]).__name__} from {self.topic}')

        if self.last_log < 0 or self.last_msg_time-self.last_log > self.log_message_every_sec:
            log_msg = True
            self.last_log = self.last_msg_time #last logged now

        for id_peer in self.peers.keys():
            dc:RTCDataChannel = self.peers[id_peer]
            if dc.readyState == 'open':
                if log_msg:
                    self.ctrl_node.get_logger().info(f'⚡️ Sending {len(self.last_msg)}B into {self.topic} for id_peer={id_peer} / dc= {str(id(dc))}, total received: {self.num_received}')
                # print(f' hello! {self.topic}')
                try:
                    # await self.event_loop.create_task(dc.send(msg)) #always raw bytes bcs fast
                    # self.event_loop.call_soon_threadsafe(dc.send, msg)
                    # def send_this(what):
                        # self.event_loop.run_in_executor(self.executor, dc.send, what)
                    # self.event_loop.call_soon_threadsafe(send_this, msg)
                    # dc.send(msg)
                    # self.event_loop.call_soon_threadsafe(dc.send, msg)
                    # self.event_loop.call_soon_threadsafe(dc.send, msg)

                    # last_unfinished = self.last_send_future is not None and not self.last_send_future.done()
                    dc.send(self.last_msg)

                    # await dc.send(msg) #always raw bytes bcs fast
                except Exception as e:
                    print(f'⚡️ Exception in on_msg: {e}')
                    traceback.print_exception(e)

            # else:
                # self.node.get_logger().info(c(f'DC {self.topic} for id_peer={id_peer} not open, not sending', 'dark_grey'))
                # self.peers.pop(id_peer)
                # dc.close()
                # self.stop(id_peer)

        # print(f' hello? {self.topic}')

        if self.on_msg_cb is not None:
            self.on_msg_cb()
        else:
            self.ctrl_node.get_logger().debug(f'on_msg_cb is {self.on_msg_cb}')


    # this might send a message twice or hang when there's nothing in the topic (exits when peer disconnects)
    # only used to ensure delivery of reliable topics
    async def report_latest_when_ready(self, id_peer:str):
        while True:
            if not id_peer in self.peers.keys():
                return
            dc:RTCDataChannel = self.peers[id_peer]

            if self.last_msg == None:
                await asyncio.sleep(.5) # wait for data
                continue

            if dc.readyState == 'open':
                # asyncio.get_event_loop().create_task(self.peers[id_peer].send())
                self.ctrl_node.get_logger().debug(f'⚡️ Sending latest {len(self.last_msg)}B msg of {self.topic} to {id_peer}')
                try:
                    dc.send(self.last_msg)
                    return #all done
                except Exception as e:
                    self.ctrl_node.get_logger().debug(f'⚡️ Exception while sending latest of {self.topic} to {id_peer}: {e}')
                    await asyncio.sleep(.5) #wait until dc opens
            else:
                await asyncio.sleep(.5) #wait until dc opens


    def stop(self, id_peer:str) -> bool:

        if id_peer in self.peers.keys():
            self.peers.pop(id_peer)

        if len(self.peers.keys()) > 0:
            return False

        if self.sub == True:
            self.reader_ctrl_queue.put_nowait({'action': 'unsubscribe', 'topic':self.topic })
        else:
            self.ctrl_node.get_logger().info(f'Destroying local subscriber for {self.topic}')
            self.ctrl_node.destroy_subscription(self.sub)

        self.sub = None
        self.topic = None

        return True #destroyed
