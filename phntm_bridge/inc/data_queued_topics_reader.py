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
from rclpy.serialization import deserialize_message, serialize_message

import rclpy

from typing import Callable
import time
from rclpy.callback_groups import CallbackGroup, MutuallyExclusiveCallbackGroup
from rclpy.context import Context
from rclpy.executors import Executor, MultiThreadedExecutor, SingleThreadedExecutor
import threading
import traceback

from tf2_msgs.msg import TFMessage
from geometry_msgs.msg import TransformStamped

import multiprocessing as mp
from multiprocessing.connection import Connection
from queue import Empty, Full

class DataQueuedTopicsSubscription:

    # def __init__(self, sub:Subscription, peers:list[str], frame_processor, processed_frames_h264:mp.Queue, processed_frames_v8:mp.Queue, make_keyframe_shared:mp.Value, make_h264_shared:mp.Value, make_v8_shared:mp.Value):
    def __init__(self, ctrl_node:Node, worker_ctrl_queue:mp.Queue,  worker_out_queue:mp.Queue, event_loop:object, blocking_reads_executor, log_message_every_sec:float, msg_blinker_cb:Callable):

        self.ctrl_node:Node = ctrl_node
        self.worker_ctrl_queue:mp.Queue = worker_ctrl_queue
        self.queue:mp.Queue = worker_out_queue # only tf

        # self.peers:{str:RTCDataChannel} = {} #target outbound dcs
        self.peers:dict[str:dict] = {} # target outbound messages here (dict by topic)
        
        self.num_received:dict[str:int] = {}
        self.last_msg:dict[str:any] = {}
        self.last_msg_time:dict[str:float] = {}
        self.last_log:dict[str:float] = {}
        self.log_message_every_sec:float = log_message_every_sec
        
        self.msg_blinker_cb:Callable = msg_blinker_cb
        self.event_loop = event_loop
        self.last_send_future:asyncio.Future = None
        
        self.executor = blocking_reads_executor #concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix=f'{topic}_read')

        self.msg_callbacks:dict[str:list] = {}
        
        self.read_task = None
        # self.send_pool = concurrent.futures.ThreadPoolExecutor()
        #print(f'DataPipedTopicSubscription:__init__() {threading.get_ident()}')

    def start(self, id_peer:str, msg_type:str, topic:str, qos:QoSProfile, peer_dc:RTCDataChannel, msg_callback=None) -> bool:

        if topic in self.peers.keys() or topic in self.msg_callbacks.keys(): # worker subscribed to this topic already
            if id_peer:
                if id_peer in self.peers[topic].keys():
                    self.ctrl_node.get_logger().info(f'Subscriber was already running for queued {topic} with peer {id_peer} subscribed! old dc_id was {str(id(self.peers[topic][id_peer]))} new dc_id is {str(id(peer_dc))}')
                    if peer_dc == self.peers[topic][id_peer]:
                        return True # all good, do not reset anything
                else:
                    self.ctrl_node.get_logger().info(f'Subscriber was already running for queued {topic}, adding peer {id_peer}, dc_id={str(id(peer_dc))}')    
                self.peers[topic][id_peer] = peer_dc
            if msg_callback:
                if not topic in self.msg_callbacks.keys():
                    self.msg_callbacks[topic] = []
                if not msg_callback in self.msg_callbacks[topic]: 
                    self.msg_callbacks[topic].append(msg_callback) # add callback
            return True #all done, one sub for all
            
        try:
            self.worker_ctrl_queue.put_nowait({'action': 'subscribe',
                                                'pipe': None, # using queue
                                                'topic': topic,
                                                'qos': {
                                                    'history': qos.history,
                                                    'depth': qos.depth,
                                                    'reliability': qos.reliability,
                                                    'durability': qos.durability,
                                                    'lifespan': -1 if qos.lifespan == Infinite else qos.lifespan.nanoseconds
                                                },
                                                'msg_type': msg_type
                                                })
            
            if id_peer:
                if not topic in self.peers.keys():
                    self.peers[topic] = {}    
                self.peers[topic][id_peer] = peer_dc
            if msg_callback:
                if not topic in self.msg_callbacks.keys():
                    self.msg_callbacks[topic] = []
                if not msg_callback in self.msg_callbacks[topic]: 
                    self.msg_callbacks[topic].append(msg_callback) # add callback

            # read the queue
            if not self.read_task:
                self.read_task = self.event_loop.create_task(self.read_queued_data(), name="queue_reader")

            return True
        except Exception as e:
            self.ctrl_node.get_logger().error(f'Error subscribing to queued {self.topic}: {e}')
            print(self.worker_ctrl_queue)
        return False


    # read queued tf data
    async def read_queued_data(self):
        while self.read_task and not self.read_task.cancelled():
            try:
                tfs_by_topic_and_frame = {}
                num = 0
                while self.queue.qsize() > 0: # uderliable but that's okay
                    try:
                        res = self.queue.get(False) # throws when empty
                        if 'msg' in res.keys() and res['msg'] == None: # closing queue from the worker
                            return
                        topic=res['topic']
                        if not topic in tfs_by_topic_and_frame.keys():
                            tfs_by_topic_and_frame[topic] = {}
                        tf = deserialize_message(res['msg'], TFMessage)
                        for t in tf.transforms:
                            # print(t)
                            frame_id = t.header.frame_id + '>' + t.child_frame_id
                            if (not frame_id in tfs_by_topic_and_frame[topic].keys()) \
                            or (t.header.stamp.sec > tfs_by_topic_and_frame[topic][frame_id].header.stamp.sec) \
                            or (t.header.stamp.sec == tfs_by_topic_and_frame[topic][frame_id].header.stamp.sec and t.header.stamp.nanosec > tfs_by_topic_and_frame[topic][frame_id].header.stamp.nanosec):
                                tfs_by_topic_and_frame[topic][frame_id] = t
                        # await asyncio.sleep(0.01)
                        
                    except Empty:
                        # print(f'Queue empty, got {len(tfs_by_topic_and_frame[topic].keys())} transforms')
                        break
                
                for topic in tfs_by_topic_and_frame.keys():
                    # print(f'Sending {len(tfs_by_topic_and_frame[topic])} tfs for {topic}')
                    await self.on_msg(topic, tfs_by_topic_and_frame[topic])
                    
                await asyncio.sleep(0.01)
                
            except (KeyboardInterrupt, asyncio.CancelledError):
                print(f'read_queued_data for {self.topic} got interrupt')
                return
            except Exception as e:
                self.ctrl_node.get_logger().error(f'Exception while reading latest {self.topic} from data queue: {str(e)}')
                traceback.print_exc(e)
                return


    # called either by ctrl node's process or when data is received via reader_out_queue (called on ctrl node's process)
    async def on_msg(self, topic, tf_msgs_by_frame):
        try:
            # topic=reader_res['topic']
            
            if not topic in self.num_received.keys():
                self.num_received[topic] = 0
            else:
                self.num_received[topic] += 1
            
            self.last_msg_time[topic] = time.time()    
            if not topic in self.last_log.keys():
                self.last_log[topic] = -1
            
            tf = TFMessage()
            tf.transforms = []
            for t in tf_msgs_by_frame.values():
                tf.transforms.append(t)
            
            msg = serialize_message(tf)
            self.last_msg[topic] = msg

            log_msg = False
            if log_msg or self.num_received[topic] == 1: # first data in
                self.ctrl_node.get_logger().debug(f'⚡️ Receiving transforms from {topic}')

            if self.last_log[topic] < 0 or self.last_msg_time[topic]-self.last_log[topic] > self.log_message_every_sec:
                log_msg = True
                self.last_log[topic] = self.last_msg_time[topic] #last logged now

            if topic in self.msg_callbacks.keys():
                for msg_callback in self.msg_callbacks[topic]:
                    await msg_callback(self.last_msg[topic])
                    
            if not topic in self.peers.keys():
                return # late data after unsubscribed     

            for id_peer in self.peers[topic].keys():
                dc:RTCDataChannel = self.peers[topic][id_peer]
                if dc.readyState == 'open':
                    if log_msg:
                        self.ctrl_node.get_logger().info(f'⚡️ Sending {len(tf.transforms)} tfs /  {len(self.last_msg[topic])}B into {topic} for id_peer={id_peer} / dc= {str(id(dc))}, total received: {self.num_received[topic]}')
        
                    try:
                        dc.send(self.last_msg[topic])

                    except Exception as e:
                        print(f'⚡️ Exception in queued on_msg: {e}')
                        traceback.print_exception(e)

            if len(self.peers[topic].keys()) and self.msg_blinker_cb is not None:
                self.msg_blinker_cb()
        except Exception as ee:
            self.ctrl_node.get_logger().error(f'Exception in on_msg: {ee}')
            traceback.print_exc(ee)


    # this might send a message twice or hang when there's nothing in the topic (exits when peer disconnects)
    # only used to ensure delivery of reliable topics
    async def report_latest_when_ready(self, peer:WRTCPeer, topic:str, msg_callback):
        try:
            while True:
                
                dc:RTCDataChannel = None
                if peer:
                    if not topic in self.peers.keys() or not peer.id in self.peers[topic].keys():
                        return # unsubscribed already
                    
                    if peer.pc == None or peer.pc.signalingState != 'stable':
                        await asyncio.sleep(.1) # wait for stable
                        continue
                    
                    dc:RTCDataChannel = self.peers[topic][peer.id]
                    
                if msg_callback:
                    if not topic in self.msg_callbacks.keys() or not msg_callback in self.msg_callbacks[topic]:
                        return # unsubscribed already

                if not topic in self.last_msg.keys():
                    await asyncio.sleep(.1) # wait for data
                    continue
                
                if msg_callback:
                    self.ctrl_node.get_logger().debug(f'⚡️ Sending latest {len(self.last_msg[topic])}B msg of {topic} to cb {msg_callback}')
                    await msg_callback(self.last_msg[topic])
                    
                if (dc):
                    if dc.readyState != 'open':
                        await asyncio.sleep(.1) #wait until dc opens
                        continue
                    
                    self.ctrl_node.get_logger().debug(f'⚡️ Sending latest {len(self.last_msg[topic])}B msg of {topic} to {peer.id}')
                    try:
                        # await asyncio.sleep(1.0) # wait for the dc to fully init (this could be better)
                        dc.send(self.last_msg[topic])
                        return #all done
                    except Exception as e:
                        self.ctrl_node.get_logger().debug(f'⚡️ Exception while sending latest of {topic} to {peer.id}: {e}')
                        await asyncio.sleep(.1) #wait until dc opens
        except Exception as ee:
            self.ctrl_node.get_logger().error(f'Exception in report_latest_when_ready: {ee}')
                
                
    async def stop(self, id_peer:str, topic:str, msg_callback) -> bool:

        if id_peer and topic in self.peers.keys() and id_peer in self.peers[topic].keys():
            peer_dc:RTCDataChannel = self.peers[topic].pop(id_peer)
            peer_dc.close()
            
        if msg_callback and topic in self.msg_callbacks.keys() and msg_callback in self.msg_callbacks[topic]:
            self.msg_callbacks[topic].remove(msg_callback)
            if (len(self.msg_callbacks[topic]) == 0):
                del self.msg_callbacks[topic]

        if len(self.peers.keys()) > 0 or len(self.msg_callbacks.keys()) > 0:
            return False # active subscribers

        try:
            self.worker_ctrl_queue.put_nowait({'action': 'unsubscribe', 'topic': topic })
        except Exception as e:
            print(f'Exception while sending unsubscribe of queued {topic} into ctrl queue {e}')
            pass # allow cleanup

        return True #destroyed