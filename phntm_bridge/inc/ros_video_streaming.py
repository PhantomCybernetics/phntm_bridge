
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
    def __init__(self, ctrl_node:Node, msg_type:str, worker_ctrl_queue:mp.Queue, topic:str, qos:QoSProfile, blocking_reads_executor, log_message_every_sec:float=5.0):

        self.sub:Subscription|bool = None
        self.ctrl_node:Node = ctrl_node
        self.worker_ctrl_queue:mp.Queue = worker_ctrl_queue

        self.pipe_out:Connection = None
        self.pipe_worker:Connection = None
        self.read_task:asyncio.Coroutine = None
        self.last_read_task:asyncio.Coroutine = None
        self.peers:dict = {} #target outbound dcs
        self.topic:str = topic
        self.msg_type:str = msg_type
        self.qos = qos
        
        self.num_received:int = 0
        self.last_msg:any = None
        self.last_msg_time:float = -1.0
        self.last_log:float = -1.0
        self.log_message_every_sec:float = log_message_every_sec
        
        self.logged = False
        self.ignore = False

        # self.reliability:QoSReliabilityPolicy = reliability
        # self.durability:DurabilityPolicy = durability
        # self.lifespan_sec:int = lifespan_sec
        
        self.on_msg_cb:Callable = None
        self.event_loop = asyncio.get_event_loop() # save current
        # self.last_send_future:asyncio.Future = None

        # self.hflip = hflip
        # self.vflip = vflip
        # self.bitrate = bitrate
        # self.framerate = framerate
        # self.process_depth = process_depth
        # self.clock_rate = clock_rate
        # self.time_base = time_base
        # self.time_base_fraction = fractions.Fraction(time_base, clock_rate)

        self.last_frame_tasks:dict[str:asyncio.Task] = {}
        
        # self.bridge_time_started_ns:int = bridge_time_started_ns
        
        self.executor = blocking_reads_executor

        #print(f'TopicReadSubscription:__init__() {threading.get_ident()}')

    def start(self, id_peer:str, sender:RTCRtpSender) -> bool:

        if self.sub != None: #running
            if id_peer in self.peers.keys():
                self.ctrl_node.get_logger().warn(f'Streamer was already running for {self.topic} with peer {id_peer} subscribed! old track_id was { self.peers[id_peer]["sender"]._track_id} new track_id is {sender._track_id}')
            else:
                self.ctrl_node.get_logger().info(f'Streamer was already running for {self.topic}, adding peer {id_peer}, track_id={sender._track_id}')    
            self.peers[id_peer] = {
                'sender': sender,
                'last_send_task': None,
                'first_ts': -1,
                'last_ts': -1
            }
            # print(self.peers.keys())
            return True #all done, one sub for all

        if self.worker_ctrl_queue: # subscribe on processor's process

            self.pipe_out, self.pipe_worker = mp.Pipe(duplex=False)

            try:
                self.worker_ctrl_queue.put_nowait({
                                                    'action': 'subscribe',
                                                    'pipe': self.pipe_worker,
                                                    'topic': self.topic,
                                                    'qos': {
                                                        'history': self.qos.history,
                                                        'depth': self.qos.depth,
                                                        'reliability':self.qos.reliability,
                                                        'durability':self.qos.durability,
                                                        'lifespan': -1 if self.qos.lifespan == Infinite else self.qos.lifespan.nanoseconds
                                                    },
                                                    'msg_type': self.msg_type,
                                                #    'hflip': self.hflip,
                                                #    'vflip': self.vflip,
                                                #    'bitrate': self.bitrate,
                                                #    'framerate': self.framerate,
                                                #    'clock_rate': self.clock_rate,
                                                #    'time_base': self.time_base,
                                                })
                self.sub = True
            except Exception as e:
                self.ctrl_node.get_logger().error(f'Error subscribing to {self.topic}: {e}')

            self.read_task = self.event_loop.create_task(self.read_piped_frames())

        # else: #subscribe here on ctrl node's process
        #     #print(f'TopicReadSubscription:start() {threading.get_ident()}')

        #     message_class = None
        #     try:
        #         message_class = get_message(self.msg_type)
        #     except:
        #         pass
        #     if message_class == None:
        #         self.ctrl_node.get_logger().error(f'NOT subscribing to topic {self.topic}, msg class {self.msg_type} not loaded')
        #         return False

        #     qosProfile = QoSProfile(history=QoSHistoryPolicy.KEEP_LAST, \
        #                             depth=1, \
        #                             reliability=self.reliability, \
        #                             durability=self.durability, \
        #                             lifespan=Infinite \
        #                             )
        #     self.ctrl_node.get_logger().warn(f'Subscribing to topic {self.topic} {self.msg_type}')
        #     self.sub = self.ctrl_node.create_subscription(
        #             msg_type=message_class,
        #             topic=self.topic,
        #             callback=self.on_raw_msg,
        #             qos_profile=qosProfile,
        #             raw=True,
        #         )
        #     if self.sub == None:
        #         self.ctrl_node.get_logger().error(f'Failed subscribing to topic {self.topic}, msg class={self.msg_type}, peer={id_peer}')
        #         return False

        self.peers[id_peer] = {
            'sender': sender,
            'last_send_task': None,
            'first_ts': -1,
            'last_ts': -1
        }

        return True

    def clear_pipe(self):
        self.ctrl_node.get_logger().info(f'Image reader for {self.topic} received pipe close')
        self.pipe_out.close()
        self.read_task.cancel()
        # self.executor.shutdown(False, cancel_futures=True) 

    async def read_piped_frames(self):
        while self.read_task and not self.read_task.cancelled():
            try:
                # if self.last_read_task and not self.last_read_task.done():
                #     continue
                self.last_read_task = self.event_loop.run_in_executor(self.executor, self.pipe_out.recv) #blocks
                res = await self.last_read_task
                # res = await self.last_read_task
                if 'msg' in res and res['msg'] == None: # closing pipe from the worker
                    self.clear_pipe()
                    return
                
            except (KeyboardInterrupt, asyncio.CancelledError):
                print(f'read_piped_frames for {self.topic} got err')
                return
            except pickle.UnpicklingError as e:
                self.ctrl_node.get_logger().error(f'Exception while unpickling latest from frame pipe {self.topic}: {str(e)}')
                await asyncio.sleep(0)
                continue # try again
            except EOFError:
                self.ctrl_node.get_logger().error(f'EOFError while reading latest from frame pipe {self.topic}')
                await asyncio.sleep(0)
                continue # try again
            except Exception as e:
                self.ctrl_node.get_logger().error(f'Exception while reading latest from frame pipe {self.topic}: {str(e)}')
                self.sub = None
                return
            
            await self.on_msg(res)
            await asyncio.sleep(0)


    # called either by ctrl node's process or when data is received via reader_out_queue (called on ctrl node's process)
    async def on_msg(self, reader_res:dict):
        self.num_received += 1
        # self.last_msg = reader_res['frame_bytes']
        self.last_msg_time = time.time()

        keyframe=reader_res['keyframe']
        # timestamp=reader_res['timestamp']
        frame_packets=reader_res['frame_packets']
        # video_time_base=reader_res['time_base']

        #print(f'TopicReadSubscription:on_msg() {threading.get_ident()}')
        try:
            log_msg = False
            if self.num_received == 1: # first data in
                log_msg = True
                self.ctrl_node.get_logger().debug(f'👁️  Receiving {len(frame_packets)} frame packets from {self.topic}')

            if self.last_log < 0 or self.last_msg_time-self.last_log > self.log_message_every_sec:
                log_msg = True
                self.last_log = self.last_msg_time #last logged now

            ts_frame = reader_res['sec'] * NS_TO_SEC + reader_res['nsec']
            for id_peer in dict.fromkeys(self.peers.keys(),[]):
                
                if self.peers[id_peer]['last_send_task'] \
                    and not self.peers[id_peer]['last_send_task'].done():
                    # and not keyframe:
                    continue
                
                peer_sender = self.peers[id_peer]['sender'];
                if not peer_sender.pc or peer_sender.pc.connectionState == 'failed' \
                or peer_sender.transport.state == "closed":
                    self.ctrl_node.get_logger().info(f'👁️  Sending {self.topic} to id_peer={id_peer} / id_stream= {str(peer_sender._stream_id)} failed; pc={peer_sender.pc.connectionState}, transport={peer_sender.transport.state}')
                    if peer_sender.transport.state != "closed":
                        self.event_loop.create_task(peer_sender.transport.stop())
                    del self.peers[id_peer]
                    continue

                if peer_sender.pc.connectionState != 'connected':
                    self.peers[id_peer]['first_ts'] = -1
                    self.peers[id_peer]['last_ts'] = -1
                    continue
                
                if self.peers[id_peer]['first_ts'] < 0:
                    self.peers[id_peer]['first_ts'] = ts_frame
                peer_pts = ts_frame - self.peers[id_peer]['first_ts']

                if peer_pts <= self.peers[id_peer]['last_ts']:
                    continue
                self.peers[id_peer]['last_ts'] = peer_pts
                
                if log_msg:
                    self.ctrl_node.get_logger().info(f'👁️  Sending {len(frame_packets)} pkts of {self.topic} to id_peer={id_peer} / id_stream= {str(peer_sender._stream_id)}, total received: {self.num_received}, peer_pts={peer_pts}, sender={str(id(self.peers[id_peer]))}, pc={peer_sender.pc.connectionState} transport={peer_sender.transport.state}')
                # if timestamp == 0:
                #     offset_ns = time.time_ns()-self.bridge_time_started_ns
                #     self.peers[id_peer].timestamp_origin = convert_timebase(offset_ns, SRC_VIDEO_TIME_BASE, VIDEO_TIME_BASE)

                try:
                    self.peers[id_peer]['last_send_task'] = self.event_loop.create_task(peer_sender.send_direct(frame_data=frame_packets, stamp_converted=peer_pts, keyframe=keyframe))                
                except Exception as e:
                    self.logger.error(f'👁️  Exception while sending {self.topic} to id_peer={id_peer} / id_stream= {str(peer_sender._stream_id)}, {e}; pc={peer_sender.pc.connectionState}, transport={peer_sender.transport.state}')
                    pass

            if self.on_msg_cb is not None:
                self.on_msg_cb()
            else:
                self.ctrl_node.get_logger().debug(f'on_msg_cb is {self.on_msg_cb}')
                
        except Exception as e:
            self.ctrl_node.get_logger().error(f'Error in frame read handler {e}')
        

    def stop(self, id_peer:str) -> bool:

        if id_peer in self.peers.keys():
            self.peers.pop(id_peer)

        if len(self.peers.keys()) > 0:
            return False

        if self.sub == True: #mp
            self.worker_ctrl_queue.put_nowait({'action': 'unsubscribe', 'topic': self.topic})
        else:
            self.ctrl_node.get_logger().info(f'👁️  Destroying local subscriber for {self.topic}')
            self.ctrl_node.destroy_subscription(self.sub)

        self.sub = None
        self.topic = None

        return True #destroyed
