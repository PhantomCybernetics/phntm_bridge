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

from .peer import WRTCPeer

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
    def __init__(self, ctrl_node:Node, worker_ctrl_queue:mp.Queue, topic:str, protocol:str, event_loop:object, log_message_every_sec:float, qos:QoSProfile, msg_blinker_cb:Callable):

        self.sub:Subscription|bool = None
        self.ctrl_node:Node = ctrl_node
        self.worker_ctrl_queue:mp.Queue = worker_ctrl_queue

        self.peers:{str:RTCDataChannel} = {} #target outbound dcs
        self.topic:str = topic
        self.protocol:str = protocol

        self.num_received:int = 0
        self.last_msg:any = None
        self.last_msg_time:float = -1.0
        self.last_log:float = -1.0
        
        self.log_message_every_sec:float = log_message_every_sec
        self.qos = qos
        
        self.msg_blinker_cb:Callable = msg_blinker_cb
        self.event_loop = event_loop
        self.last_send_future:asyncio.Future = None
        
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix=f'{topic}_read')

        self.msg_callbacks:list = []
        
        # self.send_pool = concurrent.futures.ThreadPoolExecutor()
        #print(f'TopicReadSubscription:__init__() {threading.get_ident()}')

    def start(self, peer:WRTCPeer, msg_callback) -> bool:

        if self.sub != None: # already subscribed
            if peer:
                self.peers[peer.id] = peer.outbound_data_channels[self.topic] # add peers dc
            if msg_callback and msg_callback not in msg_callback: 
                self.msg_callbacks.append(msg_callback) # add callback
            return True #all done, one sub for all

        if self.worker_ctrl_queue: # subscribe on processor's process

            self.pipe_out, self.pipe_worker = mp.Pipe()
            # no_skip:bool = self.reliability == QoSReliabilityPolicy.RELIABLE
            try:
                self.worker_ctrl_queue.put_nowait({'action': 'subscribe',
                                                'pipe': self.pipe_worker,
                                                'topic': self.topic,
                                                'qos': {
                                                        'history': self.qos.history,
                                                        'depth': self.qos.depth,
                                                        'reliability':self.qos.reliability,
                                                        'durability':self.qos.durability,
                                                        'lifespan': -1 if self.qos.lifespan == Infinite else self.qos.lifespan.nanoseconds
                                                },
                                                'msg_type': self.protocol
                                                })
                self.sub = True
            except Exception as e:
                self.ctrl_node.get_logger().error(f'Error subscribing to {self.topic}: {e}')
            
            self.read_task = self.event_loop.create_task(self.read_piped_data(), name="pipe_reader")

        if peer:
            self.peers[peer.id] = peer.outbound_data_channels[self.topic] # add peer's dc
        if msg_callback and msg_callback not in self.msg_callbacks:
            self.msg_callbacks.append(msg_callback) # add callback

        return True

    def clear_pipe(self):
        self.ctrl_node.get_logger().info(f'Topic reader for {self.topic} received pipe close')
        self.pipe_out.close()
        self.read_task.cancel()
        self.executor.shutdown(False, cancel_futures=True) 


    async def read_piped_data(self):
        while self.read_task and not self.read_task.cancelled():
            try:
                res = await self.event_loop.run_in_executor(self.executor, self.pipe_out.recv) #blocks
                if res['msg'] != None:
                    await self.on_msg(res)
                else: # closing pipe from the worker
                    self.clear_pipe()
                    return
            except (KeyboardInterrupt, asyncio.CancelledError):
                print(f'read_piped_data for {self.topic} got interrupt')
                return
            except Exception as e:
                self.ctrl_node.get_logger().error(f'Exception while reading latest {self.topic} from data pipe: {str(e)}')
                traceback.print_exc(e)

    # called either by ctrl node's process or when data is received via reader_out_queue (called on ctrl node's process)
    async def on_msg(self, reader_res:dict):
        self.num_received += 1
        self.last_msg = reader_res['msg']
        self.last_msg_time = time.time()

        #print(f'TopicReadSubscription:on_msg() {threading.get_ident()}')

        log_msg = False
        if log_msg or self.num_received == 1: # first data in
            self.ctrl_node.get_logger().debug(f'⚡️ Receiving {type(reader_res["msg"]).__name__} from {self.topic}')

        if self.last_log < 0 or self.last_msg_time-self.last_log > self.log_message_every_sec:
            log_msg = True
            self.last_log = self.last_msg_time #last logged now

        for msg_callback in self.msg_callbacks:
            await msg_callback(self.last_msg)

        for id_peer in self.peers.keys():
            peer_dc:RTCDataChannel = self.peers[id_peer]
            if peer_dc.readyState == 'open':
                if log_msg:
                    self.ctrl_node.get_logger().info(f'⚡️ Sending {len(self.last_msg)}B into {self.topic} for id_peer={id_peer} / dc= {str(id(peer_dc))}, total received: {self.num_received}')
    
                try:
                    peer_dc.send(self.last_msg)

                except Exception as e:
                    print(f'⚡️ Exception in on_msg: {e}')
                    traceback.print_exception(e)

        if len(self.peers.keys()) and self.msg_blinker_cb is not None:
            self.msg_blinker_cb()


    # this might send a message twice or hang when there's nothing in the topic (exits when peer disconnects)
    # only used to ensure delivery of reliable topics
    async def report_latest_when_ready(self, peer:WRTCPeer, msg_callback):
        while True:
            
            dc:RTCDataChannel = None
            if peer:
                if not peer.id in self.peers.keys():
                    return # unsubscribed already
                
                if peer.pc == None or peer.pc.signalingState != 'stable':
                    await asyncio.sleep(.1) # wait for stable
                    continue
                
                dc:RTCDataChannel = self.peers[peer.id]
            if msg_callback:
                if not msg_callback in self.msg_callbacks:
                    return # unsubscribed already

            if self.last_msg == None:
                await asyncio.sleep(.1) # wait for data
                continue
            
            if msg_callback:
                self.ctrl_node.get_logger().debug(f'⚡️ Sending latest {len(self.last_msg)}B msg of {self.topic} to cb {msg_callback}')
                await msg_callback(self.last_msg)
            if (dc):
                if dc.readyState != 'open':
                    await asyncio.sleep(.1) #wait until dc opens
                    continue
                
                self.ctrl_node.get_logger().debug(f'⚡️ Sending latest {len(self.last_msg)}B msg of {self.topic} to {peer.id}')
                try:
                    # await asyncio.sleep(1.0) # wait for the dc to fully init (this could be better)
                    dc.send(self.last_msg)
                    return #all done
                except Exception as e:
                    self.ctrl_node.get_logger().debug(f'⚡️ Exception while sending latest of {self.topic} to {peer.id}: {e}')
                    await asyncio.sleep(.1) #wait until dc opens
        
                
    async def stop(self, peer:WRTCPeer, msg_callback) -> bool:

        if peer and peer.id in self.peers.keys():
            peer_dc:RTCDataChannel = self.peers.pop(peer.id)
            peer_dc.close()
            
        if msg_callback and msg_callback in self.msg_callbacks:
            self.msg_callbacks.remove(msg_callback)

        if len(self.peers.keys()) > 0 or len(self.msg_callbacks) > 0:
            return False # active subscribers

        if self.sub == True:
            try:
                self.worker_ctrl_queue.put_nowait({'action': 'unsubscribe', 'topic':self.topic })
            except Exception as e:
                print(f'Exception while sending unsubscribe of {self.topic} into ctrl queue {e}')
                pass # allow cleanup
        else:
            self.ctrl_node.get_logger().info(f'Destroying local subscriber for {self.topic}')
            self.ctrl_node.destroy_subscription(self.sub)
        
        self.sub = None

        return True #destroyed