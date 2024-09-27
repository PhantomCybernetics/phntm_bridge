
from aiortc.contrib.media import MediaStreamTrack
from typing import List

import fractions
from typing import Tuple, Union
import time
import asyncio
import av
import time
import logging
import traceback
import sys
import concurrent.futures

import pickle
import marshal

import numpy as np
import cv2
from aiortc.codecs import Vp8Encoder
from aiortc.codecs import H264Encoder

from ffmpeg_image_transport_msgs.msg import FFMPEGPacket
from av.packet import Packet

from termcolor import colored as c

from aiortc.codecs.h264 import DEFAULT_BITRATE, MIN_BITRATE, MAX_BITRATE
DEFAULT_BITRATE = MAX_BITRATE

from termcolor import colored

from av.frame import Frame
from av.video.frame import VideoFrame

from sensor_msgs.msg import Image
from rclpy.serialization import deserialize_message
from sensor_msgs.msg import Image
from rosidl_runtime_py.utilities import get_message
# from aiortc.rtcrtpsender import RTCEncodedFrame
from rclpy.node import Node, Parameter, QoSProfile, Publisher
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy

encoder_h264:H264Encoder = None

# AUDIO_PTIME = 0.020  # 20ms audio packetization
# VIDEO_CLOCK_RATE = 1000000000 #ns to s
# # VIDEO_PTIME = 1 / 30  # 30fps
# VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)

class MediaStreamError(Exception):
    pass

import rclpy
from rclpy.node import Node, Parameter, Subscription, QoSProfile, Publisher

from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy
from rclpy.duration import Duration, Infinite

import multiprocessing as mp
from multiprocessing.connection import Connection
from queue import Empty, Full

from aiortc import RTCRtpSender

from typing import Callable

from aiortc.mediastreams import VIDEO_TIME_BASE, convert_timebase
NS_TO_SEC = 1000000000
SRC_VIDEO_TIME_BASE = fractions.Fraction(1, NS_TO_SEC)

def IsImageType(s:str) -> bool:
    return s == ImageTopicReadSubscription.MSG_TYPE or \
           s == ImageTopicReadSubscription.COMPRESSED_MSG_TYPE or \
           s == ImageTopicReadSubscription.STREAM_MSG_TYPE

def IsEncodedStreamType(s:str) -> bool:
    return s == ImageTopicReadSubscription.STREAM_MSG_TYPE

class ImageTopicReadSubscription:

    MSG_TYPE:str = 'sensor_msgs/msg/Image'
    COMPRESSED_MSG_TYPE:str = 'sensor_msgs/msg/CompressedImage'
    STREAM_MSG_TYPE:str = 'ffmpeg_image_transport_msgs/msg/FFMPEGPacket'
    
    # def __init__(self, sub:Subscription, peers:list[str], frame_processor, processed_frames_h264:mp.Queue, processed_frames_v8:mp.Queue, make_keyframe_shared:mp.Value, make_h264_shared:mp.Value, make_v8_shared:mp.Value):
    def __init__(self, ctrl_node:Node, worker_type:str, worker_ctrl_queue:mp.Queue, worker_out_queue: mp.Queue, blocking_reads_executor, log_message_every_sec:float=5.0, on_msg_cb:Callable=None):

        self.ctrl_node:Node = ctrl_node
        self.worker_ctrl_queue:mp.Queue = worker_ctrl_queue
        self.worker_type:str = worker_type
        
        self.queue:mp.Queue = worker_out_queue
        self.read_task:asyncio.Coroutine = None
        self.last_read_task:asyncio.Coroutine = None
        self.peers:dict[str:dict] = {} # target outbound messages here (dict by topic)
        
        self.num_received:int = 0
        self.last_msg:any = None
        self.last_msg_time:float = -1.0
        self.last_log:float = -1.0
        self.log_message_every_sec:float = log_message_every_sec
        
        self.logged = False
        self.ignore = False
        
        self.on_msg_cb:Callable = on_msg_cb
        self.event_loop = asyncio.get_event_loop() # save current

        # self.last_frame_tasks:dict[str:asyncio.Task] = {}
        
        self.executor = blocking_reads_executor


    def start(self, id_peer:str, msg_type:str, topic:str, qos:QoSProfile, peer_sender:RTCRtpSender) -> bool:

        if topic in self.peers.keys(): # worker subscribed to his topic already
            if id_peer in self.peers[topic].keys():
                self.ctrl_node.get_logger().warn(f'Streamer was already running for {topic} with peer {id_peer} subscribed! old track_id was { self.peers[topic][id_peer]["sender"]._track_id} new track_id is {peer_sender._track_id}')
            else:
                self.ctrl_node.get_logger().info(f'Streamer was already running for {topic}, adding peer {id_peer}, track_id={peer_sender._track_id}')    
            self.peers[topic][id_peer] = {
                'sender': peer_sender,
                'last_send_task': None,
                'first_ts': -1,
                'last_ts': -1
            }
            # print(self.peers.keys())
            return True #all done, one sub for all
    
        try:
            self.worker_ctrl_queue.put_nowait({
                                                'action': 'subscribe',
                                                'pipe': None,
                                                # 'queue': self.queue,
                                                'topic': topic,
                                                'qos': {
                                                    'history': qos.history,
                                                    'depth': qos.depth,
                                                    'reliability': qos.reliability,
                                                    'durability': qos.durability,
                                                    'lifespan': -1 if qos.lifespan == Infinite else qos.lifespan.nanoseconds
                                                },
                                                'msg_type': msg_type,
                                            #    'hflip': self.hflip,
                                            #    'vflip': self.vflip,
                                            #    'bitrate': self.bitrate,
                                            #    'framerate': self.framerate,
                                            #    'clock_rate': self.clock_rate,
                                            #    'time_base': self.time_base,
                                            })
            if not topic in self.peers.keys():
                self.peers[topic] = {}
                
            self.peers[topic][id_peer] = {
                'sender': peer_sender,
                'last_send_task': None,
                'first_ts': -1,
                'last_ts': -1
            }
        except Exception as e:
            self.ctrl_node.get_logger().error(f'Error subscribing to {topic}: {e}')

        if not self.read_task:
            self.read_task = self.event_loop.create_task(self.read_worker_frames())

        return True

    # def clear_pipe(self):
    #     self.ctrl_node.get_logger().info(f'Image reader for {self.topic} received pipe close')
    #     self.pipe_out.close()
    #     self.read_task.cancel()
    # # self.executor.shutdown(False, cancel_futures=True) 

    async def read_worker_frames(self):
        self.time_deserialization = 0.0
        self.time_sending = 0.0
        self.num_samples = 0
        while self.read_task and not self.read_task.cancelled():
            try:
                
                self.last_read_task = self.event_loop.run_in_executor(self.executor, self.queue.get) #blocks
                res_bytes = await self.last_read_task
                start = time.time()
                res = marshal.loads(res_bytes)
                self.time_deserialization += time.time() - start
                # res = await self.last_read_task
                if 'msg' in res and res['msg'] == None: # closing pipe from the worker
                    self.clear_pipe()
                    return
                
            except (KeyboardInterrupt, asyncio.CancelledError):
                print(f'read_piped_frames for {self.topic} got err')
                return
            except pickle.UnpicklingError as e:
                self.ctrl_node.get_logger().error(f'Exception while unpickling latest from frame queue: {str(e)}')
                await asyncio.sleep(0)
                continue # try again
            except EOFError:
                self.ctrl_node.get_logger().error(f'EOFError while reading latest from frame queue')
                await asyncio.sleep(0)
                continue # try again
            except Exception as e:
                self.ctrl_node.get_logger().error(f'Exception while reading latest from frame queue: {str(e)}')
                self.sub = None
                return
            
            start = time.time()
            await self.on_msg(res)
            self.time_sending += time.time() - start
            
            self.num_samples += 1
            if self.num_samples == 1000:
                self.ctrl_node.get_logger().info(f'Avg recv deserialization time: {self.time_deserialization:.20f}s')
                self.ctrl_node.get_logger().info(f'Avg recv send time: {self.time_sending:.20f}s')
                self.time_deserialization = 0.0
                self.time_sending = 0.0
                self.num_samples = 0
                

    # called when data is received via reader_out_queue (called on ctrl node's process)
    async def on_msg(self, reader_res:dict):
        self.num_received += 1
        # self.last_msg = reader_res['frame_bytes']
        self.last_msg_time = time.time()

        keyframe=reader_res['keyframe']
        # timestamp=reader_res['timestamp']
        frame_packets=reader_res['frame_packets']
        topic=reader_res['topic'] if 'topic' in reader_res.keys() else None
        # video_time_base=reader_res['time_base']

        try:
            if not topic in self.peers.keys():
                return # late data when unsubscribed
            
            log_msg = False
            if self.num_received == 1: # first data in
                log_msg = True
                self.ctrl_node.get_logger().debug(f'👁️  Receiving {len(frame_packets)} frame packets from {topic}')

            if self.last_log < 0 or self.last_msg_time-self.last_log > self.log_message_every_sec:
                log_msg = True
                self.last_log = self.last_msg_time #last logged now

            ts_frame = reader_res['ts'] # 1/9000
            for id_peer in dict.fromkeys(self.peers[topic].keys(),[]):
                
                peer_sender = self.peers[topic][id_peer]['sender'];
                if not peer_sender.pc or peer_sender.pc.connectionState == 'failed' \
                or peer_sender.transport.state == "closed":
                    self.ctrl_node.get_logger().info(f'👁️  Sending {self.topic} to id_peer={id_peer} / id_stream= {str(peer_sender._stream_id)} failed; pc={peer_sender.pc.connectionState}, transport={peer_sender.transport.state}')
                    if peer_sender.transport.state != "closed":
                        self.event_loop.create_task(peer_sender.transport.stop())
                    del self.peers[topic][id_peer]
                    continue

                if peer_sender.pc.connectionState != 'connected':
                    self.peers[topic][id_peer]['first_ts'] = -1
                    self.peers[topic][id_peer]['last_ts'] = -1
                    continue
                
                if self.peers[topic][id_peer]['first_ts'] < 0:
                    self.peers[topic][id_peer]['first_ts'] = ts_frame
                peer_pts = ts_frame - self.peers[topic][id_peer]['first_ts']

                if peer_pts <= self.peers[topic][id_peer]['last_ts']:
                    continue
                self.peers[topic][id_peer]['last_ts'] = peer_pts
                
                if log_msg:
                    self.ctrl_node.get_logger().info(f'👁️  Sending {len(frame_packets)} pkts of {topic} to id_peer={id_peer} / id_stream= {str(peer_sender._stream_id)}, total received: {self.num_received}, peer_pts={peer_pts}, sender={str(id(self.peers[topic][id_peer]))}, pc={peer_sender.pc.connectionState} transport={peer_sender.transport.state}')
                    
                try:
                    self.peers[topic][id_peer]['last_send_task'] = self.event_loop.create_task(peer_sender.send_direct(frame_data=frame_packets, stamp_converted=peer_pts, keyframe=keyframe))                
                except Exception as e:
                    self.logger.error(f'👁️  Exception while sending {topic} to id_peer={id_peer} / id_stream= {str(peer_sender._stream_id)}, {e}; pc={peer_sender.pc.connectionState}, transport={peer_sender.transport.state}')
                    pass

            if self.on_msg_cb is not None:
                self.on_msg_cb()
                
        except Exception as e:
            self.ctrl_node.get_logger().error(f'Error in frame read handler {self.worker_type} {e}')
        

    def stop(self, id_peer:str, topic:str) -> bool:

        if id_peer in self.peers[topic].keys():
            self.peers[topic].pop(id_peer)

        if len(self.peers[topic].keys()) == 0:
            self.peers.pop(topic)
            self.worker_ctrl_queue.put_nowait({'action': 'unsubscribe', 'topic': topic})

        if len(self.peers.keys()) > 0:
            return False # still some topics subscribed

        return True # all clear, destroy
