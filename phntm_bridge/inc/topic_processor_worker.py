import asyncio
import concurrent.futures

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel, RTCConfiguration, RTCIceServer

from rclpy.node import Node, Parameter, Subscription, QoSProfile, Publisher
from rclpy.duration import Duration, Infinite
from rclpy.impl.rcutils_logger import RcutilsLogger
from rosidl_runtime_py.utilities import get_message, get_interface
from rclpy.callback_groups import CallbackGroup
from rclpy.constants import S_TO_NS
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy
from rcl_interfaces.msg import ParameterDescriptor, ParameterType, FloatingPointRange, IntegerRange
import pickle
import marshal
from rclpy.callback_groups import ReentrantCallbackGroup

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
from multiprocessing.connection import Connection

from sensor_msgs.msg import Image, CompressedImage
from ffmpeg_image_transport_msgs.msg import FFMPEGPacket
from rclpy.serialization import deserialize_message

from .ros_video_streaming import ImageTopicReadSubscription

import numpy as np
import cv2
from aiortc.codecs import H264Encoder
from aiortc.codecs.h264 import DEFAULT_BITRATE, MIN_BITRATE, MAX_BITRATE
from av.frame import Frame
from av.packet import Packet
from av.video.frame import VideoFrame
import fractions
import yaml

NS_TO_SEC = 1000000000

# runs as a separate process for bette isolation and lower ctrl/cam latency
def TopicProcessorWorker(running_shared:mp.Value, reader_label:str, ctrl_queue:mp.Queue, out_queue:mp.Queue):

    print(f'Topic Reader {reader_label}: starting')
    
    yaml_fname = '/ros2_ws/phntm_bridge_params.yaml'
    with open(yaml_fname, 'r') as file:
        yaml_config = yaml.safe_load(file)

    print(f'Worker {reader_label} loaded config from {str(yaml_fname)}')

    rcl_context = rclpy.context.Context()
    rcl_context.init()
    rclpy_executor = SingleThreadedExecutor(context=rcl_context)
    reader_node = Node(node_name=f'phntm_worker_{reader_label}',
                       context=rcl_context,
                       enable_rosout=False,
                       use_global_arguments=False)
    rclpy_executor.add_node(reader_node);
    reader_node.get_logger().set_level(rclpy.logging.LoggingSeverity.DEBUG)

    w = Worker(reader_node, rclpy_executor, reader_label, running_shared, ctrl_queue, out_queue, yaml_config)
    try:
        asyncio.run(w.worker_loop())
    except (asyncio.CancelledError, KeyboardInterrupt):
        reader_node.get_logger().info(c(f'Shutting down', 'red'))
        pass
    except Exception as e:
        reader_node.get_logger().error(f'Exception in worker loop:', e)

    reader_node.get_logger().warn(f'Stopping')

    # rclpy.shutdown()
    

class Worker:
    
    def __init__(self, reader_node, rclpy_executor, reader_label, running_shared, ctrl_queue, out_queue, yaml_config) -> None:
        self.reader_node = reader_node
        self.rclpy_executor = rclpy_executor
        self.reader_label = reader_label
        self.running_shared = running_shared
        self.ctrl_queue = ctrl_queue
        self.out_queue = out_queue
        self.logger = self.reader_node.get_logger()
        self.active_subs:dict[str:dict] = {}
        self.newest_messages_by_topic:dict[str:list] = {}
        self.yaml_config = yaml_config['/**']['ros__parameters']
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
        self.rcl_cg = ReentrantCallbackGroup()
        self.time_deserialization = 0.0
        self.time_packet_init = 0.0
        self.time_packetization = 0.0
        self.time_serialization = 0.0
        self.num_samples = 0  
        
        self.node_spin_task = None
        self.out_queue_lock = threading.Lock()
        
    
    async def spin_node_loop(self):
        self.logger.info(f'Spining the node...')
        while self.running_shared.value > 0:
            rclpy.spin_once(self.reader_node, executor=self.rclpy_executor, timeout_sec=0.1)
            await asyncio.sleep(0)
        self.logger.info(f'Done spinning node')


    async def worker_loop(self):
        
        try:
            if not self.node_spin_task:
                self.reader_node.get_logger().info(f'Spinning the node...')
                self.node_spin_task = asyncio.get_event_loop().create_task(self.spin_node_loop())
        except Exception as e:
            self.reader_node.get_logger().error(f'Exception while spinning reader node: {e}')
        
        #recieve cmd messages
        while self.running_shared.value > 0:
            while True:
                try:
                    ctrl_cmd = self.ctrl_queue.get(block=False)
                    await self.on_cmd(ctrl_cmd)
                except Empty:
                    break # all cmd messages processed
            await asyncio.sleep(0.1) # cmds can go slower


    async def on_cmd(self, ctrl_cmd:dict):

        topic = ctrl_cmd['topic']

        # unsubscribe
        if ctrl_cmd['action'] == 'unsubscribe' and topic in self.active_subs.keys():
            try:
                self.logger.warn(f'Removing local subscriber for {topic}')
            
                self.active_subs[topic]['ignore'] = True # ignore any new data
                self.reader_node.destroy_subscription(self.active_subs[topic]['sub'])
                if 'push_task' in self.active_subs[topic].keys() and self.active_subs[topic]['push_task'] and not self.active_subs[topic]['push_task'].done():
                    self.logger.info(f'Cancelling unfinished push task for {topic}')
                    self.active_subs[topic]['push_task'].cancel()
                if 'pipe' in self.active_subs[topic].keys() and self.active_subs[topic]['pipe']:
                    self.logger.info(f'Closing pipe for {topic}')
                    self.active_subs[topic]['push_task'] = asyncio.get_event_loop().run_in_executor(self.active_subs[topic]['executor'], self.active_subs[topic]['pipe'].send, {
                        'topic': topic,
                        'msg': None # = closing pipe
                    }) # blocks until read
                    self.active_subs[topic]['push_task'].add_done_callback(lambda f: self.pipe_close(f, topic))
                self.active_subs[topic]['loop'].call_soon_threadsafe(self.active_subs[topic]['loop'].stop)
                self.active_subs[topic]['thread'].join()
                del self.active_subs[topic]
            except Exception as e:
                self.logger.error(f'Failed unsubscribing from topic {topic}: {e}')

        # subscribe 
        if ctrl_cmd['action'] == 'subscribe' and not topic in self.active_subs.keys():
            msg_type = ctrl_cmd['msg_type']

            qosProfile = QoSProfile(
                history=ctrl_cmd['qos']['history'],
                depth=ctrl_cmd['qos']['depth'],
                reliability=ctrl_cmd['qos']['reliability'],
                durability=ctrl_cmd['qos']['durability'],
                lifespan=Infinite if ctrl_cmd['qos']['lifespan'] < 0 else Duration(nanoseconds=ctrl_cmd['qos']['lifespan'])
            )

            pipe = ctrl_cmd['pipe'] if 'pipe' in ctrl_cmd.keys() else None
            # queue = ctrl_cmd['queue'] if 'queue' in ctrl_cmd.keys() else None

            message_class = None
            try:
                message_class = get_message(msg_type)
            except:
                pass
            if message_class == None:
                self.logger.error(f'NOT subscribing to topic {topic}, msg class {msg_type} not loaded')
                return

            def handler_error_catcher(f):
                try:
                    ee = f.exception()
                    if ee:
                        self.logger.error(f'Msg handler err {topic}: {ee}\n{traceback.format_exception(ee)}')
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    self.logger.error(f'Handler output err while catching errors {topic}: {e}')
            
            try:
                self.logger.info(c(f'Subscribing to topic {topic} {msg_type} qosProfile={qosProfile}', 'cyan'))
                
                topic_loop = asyncio.new_event_loop()
                topic_thread = threading.Thread(target=self.run_topic_loop, name=f'loop_{topic}', args=(topic,topic_loop))
                topic_thread.start()
                
                cb = None
                if msg_type == ImageTopicReadSubscription.STREAM_MSG_TYPE:
                    def on_msg_cb(msg:any):
                        try:
                            f = asyncio.run_coroutine_threadsafe(self.on_stream_image_data(topic, msg), topic_loop)
                            f.add_done_callback(handler_error_catcher)
                        except Exception as e:
                            self.logger.error(f'Error handlind data of {topic}: {e}')
                    cb = on_msg_cb
                elif msg_type == ImageTopicReadSubscription.MSG_TYPE:
                    def on_msg_cb(msg:any):
                        try:
                            f = asyncio.run_coroutine_threadsafe(self.on_raw_image_data(topic, msg), topic_loop)
                            f.add_done_callback(handler_error_catcher)
                        except Exception as e:
                            self.logger.error(f'Error handlind data of {topic}: {e}')
                    cb = on_msg_cb
                elif msg_type == ImageTopicReadSubscription.COMPRESSED_MSG_TYPE:
                    def on_msg_cb(msg:any):
                        try:
                            f = asyncio.run_coroutine_threadsafe(self.on_compressed_image_data(topic, msg), topic_loop)
                            f.add_done_callback(handler_error_catcher)
                        except Exception as e:
                            self.logger.error(f'Error handlind data of {topic}: {e}')
                    cb = on_msg_cb
                else: # data
                    def on_msg_cb(msg:any):
                        try:
                            f = asyncio.run_coroutine_threadsafe(self.on_data(topic, msg), topic_loop)
                            f.add_done_callback(handler_error_catcher)
                        except Exception as e:
                            self.logger.error(f'Error handlind data of {topic}: {e}')
                    cb = on_msg_cb
                
                sub = self.reader_node.create_subscription(
                                msg_type=message_class,
                                topic=topic,
                                callback=cb,
                                qos_profile=qosProfile,
                                raw=True,
                            )
                if not sub:
                     self.logger.error(f'Failed subscribing to topic {topic}, msg class={msg_type}')
                     return
                
                args = ctrl_cmd
                for key in [ 'action', 'reliability', 'durability', 'pipe']:
                    if key in args.keys():
                        args.pop(key)

                self.active_subs[topic] = {
                    'sub': sub,
                    'args': args,
                    'pipe': pipe, # data only
                    'push_task': None,
                    'logged': False,
                    'executor': self.executor,
                    'loop': topic_loop,
                    'thread': topic_thread
                }
            except Exception as ee:
                self.logger.error(f'Failed subscribing to topic {topic}, msg class={msg_type}: {ee}')


    # trhead for each individual topic
    def run_topic_loop(self, topic, loop):
        self.logger.info(f'Running loop thread for {topic}')
        asyncio.set_event_loop(loop) # set default loop for the trhead
        loop.run_forever()
        self.logger.info(f'Stopped loop thread for {topic}')
    
    
    async def on_data(self, topic:str, msg:any):

        if not topic in self.active_subs.keys():
            return
        
        sub = self.active_subs[topic]
        
        if 'ignore' in sub:
            return
        
        # if sub['push_task'] and not sub['push_task'].done():
        #     return # dropping data here
        
        def pipe_error_catcher(f):
            try:
                ee = f.exception()
                if ee:
                    self.logger.error(f'Pipe output err {topic}: {ee}')
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.logger.error(f'Pipe output err while catching errors {topic}: {e}')
        
        sub['push_task'] = asyncio.get_event_loop().run_in_executor(sub['executor'], sub['pipe'].send, {
            'topic': topic,
            'msg': msg
        }) # blocks until read, no more frames of this topic are processed until then
        sub['push_task'].add_done_callback(pipe_error_catcher)
        await sub['push_task']
        
    
    def pipe_close(self, f, topic):
        try:
            e = f.exception()
            if e:
                self.logger.error(f'Pipe close {topic} err: {e}')
            self.active_subs[topic]['pipe'].close()
            del self.active_subs[topic]
        except Exception as ee:
            self.logger.error(f'Pipe close {topic} exception {topic}: {ee}')


    # hw (or elsewhere) encoded frames
    async def on_stream_image_data(self, topic:str, msg:any):
        
        if not topic in self.active_subs.keys():
            return
        
        sub = self.active_subs[topic]
        
        if 'ignore' in sub:
            return
        
        try:
            frame:FFMPEGPacket = deserialize_message(msg, FFMPEGPacket)
            size = len(frame.data)
        except Exception as e:
            self.logger.error(f'Error deserializing frame of {topic}, msg size={len(msg)}B: {e}')
            return
        
        if not sub['logged']:
            sub['logged'] = True
            self.logger.info(f'Processing stream frame data for {topic}, encoding={frame.encoding} size={size}B')
            
        if size == 0:
            return

        is_keyframe = (frame.flags == 1)
        stamp_ns_raw = int(frame.header.stamp.sec * NS_TO_SEC) + int(frame.header.stamp.nanosec)
    
        # expecting fully encoded frames here and only need to packetize them for transport
        try:
            p = Packet(frame.data)
            p.pts = stamp_ns_raw
            p.time_base = fractions.Fraction(1, NS_TO_SEC) # will be converted to 1/90000
        except Exception as e:
            self.logger.error(f'Error casting frame to packet for {topic}, data size size={len(frame.data)}B: {e}')
            return

        try:
            if 'encoder' not in sub.keys():
                sub['encoder'] = H264Encoder()
            packets, ts = sub['encoder'].pack(p)
        except Exception as e:
            self.logger.error(f'Error packing frame of {topic}, data size size={len(frame.data)}B: {e}')
            return

        try:
            frame_transport = marshal.dumps({
                'topic': topic,
                'frame_packets': packets,
                'ts': ts,
                'keyframe': is_keyframe,
            })
        except Exception as e:
            self.logger.error(f'Error pushing frame of {topic}, data size size={len(frame.data)}B: {e}')
            return
        
        self.out_queue_lock.acquire()
        try:
            self.out_queue.put_nowait(frame_transport)
        except Exception as e:
            self.logger.error(f'Error putting frame of {topic} into queue, data size size={len(frame.data)}B: {e}')
        self.out_queue_lock.release()


    # software encoded frames
    async def on_raw_image_data(self, topic:str, msg:any):

        if not topic in self.active_subs.keys():
            return
        
        sub = self.active_subs[topic]
        
        if 'ignore' in sub:
            return

        try:
            im:Image = deserialize_message(msg, Image)
            size = len(im.data)
        except Exception as e:
            self.logger.error(f'Error deserializing frame of {topic}, msg size={len(msg)}B: {e}')
            return
        
        if not sub['logged']:
            sub['logged'] = True
            self.logger.info(c(f'Processing raw frame data for {topic}, encoding={im.encoding} size={size}B; ', 'cyan'))

        if size == 0:
            return

        max_sensor_value = 255.0
        cv_colormap = 0
        if im.encoding == '16UC1' or im.encoding == 'mono16' or im.encoding == '32FC1':
            try:
                colormap = self.yaml_config[topic][f'{im.encoding}_colormap'] if topic in self.yaml_config.keys() and f'{im.encoding}_colormap' in self.yaml_config[topic].keys() else cv2.COLORMAP_MAGMA
                self.reader_node.declare_parameter(f'{topic}.{im.encoding}_colormap', colormap, descriptor=ParameterDescriptor(
                    type=ParameterType.PARAMETER_INTEGER,
                    description='(Depth only) cv2.COLORMAP for colorization',
                    integer_range=[ IntegerRange(
                        from_value=0,
                        to_value=21
                    ) ]
                ))
                max_sensor_value = self.yaml_config[topic][f'{im.encoding}_max_sensor_value'] if topic in self.yaml_config.keys() and f'{im.encoding}_max_sensor_value' in self.yaml_config[topic].keys() else 255.0
                self.reader_node.declare_parameter(f'{topic}.{im.encoding}_max_sensor_value', max_sensor_value, descriptor=ParameterDescriptor(
                    type=ParameterType.PARAMETER_DOUBLE,
                    description='(Depth only) The maximum sensor value (max distance or brightness)'
                )) # 2m (units depend on sensor)
            except rclpy.exceptions.ParameterAlreadyDeclaredException:
                pass
            max_sensor_value =  self.reader_node.get_parameter(f'{topic}.{im.encoding}_max_sensor_value').get_parameter_value().double_value
            cv_colormap =  self.reader_node.get_parameter(f'{topic}.{im.encoding}_colormap').get_parameter_value().integer_value

        if im.encoding == 'rgb8':
            channels = 3  # 3 for RGB format
            np_array = np.frombuffer(im.data, dtype=np.uint8) # Convert the image data to a NumPy array
            np_array = np_array.reshape(im.height, im.width, channels) # Reshape the array based on the image dimensions
        elif im.encoding == 'bgr8':
            channels = 3  # 3 for RGB format
            np_array = np.frombuffer(im.data, dtype=np.uint8) # Convert the image data to a NumPy array
            b,g,r = np_array[::3,], np_array[1::3,], np_array[2::3]
            np_array = cv2.merge([r,g,b])
            np_array = np_array.reshape(im.height, im.width, channels) # Reshape the array based on the image dimensions
        elif im.encoding == '16UC1' or im.encoding == 'mono16': # channels = 1  # 3 for RGB format
            np_array = np.frombuffer(im.data, dtype=np.uint16) * float(255.0 / max_sensor_value)
            np_array = np.uint8 (np_array)
            np_array = cv2.applyColorMap(np_array, cv_colormap)
            np_array = np_array.reshape(im.height, im.width, 3)
        elif im.encoding == '32FC1': # channels = 1  # 3 for RGB format
            np_array = np.frombuffer(im.data, dtype=np.float32) * (255.0 / max_sensor_value) #;).astype(np.uint16)
            np_array = np.uint8 (np_array)
            np_array = cv2.applyColorMap(np_array, cv_colormap)
            np_array = np_array.reshape(im.height, im.width, 3)
        else:
            self.logger.error(f'Received unsupported frame encoding for {topic}:  enc="{im.encoding}" is_bigendian={im.is_bigendian} {im.width}x{im.height} data={len(im.data)}B step={im.step}, not processing')
            sub['ignore'] = True
            return
        
        # software encode h264
        
        stamp_ns_raw = (im.header.stamp.sec * NS_TO_SEC) + im.header.stamp.nanosec
        
        frame = VideoFrame.from_ndarray(np_array, format="rgb24")
        frame.pts = stamp_ns_raw
        frame.time_base = fractions.Fraction(1, NS_TO_SEC) # will be converted to 1/90000

        is_keyframe = 'last_keyframe_stamp_ns' not in sub.keys() \
            or stamp_ns_raw - sub['last_keyframe_stamp_ns'] >= NS_TO_SEC # make first keyframe, then every second

        try:
            if 'encoder' not in sub.keys():
                sub['encoder'] = H264Encoder()
            packets, ts = sub['encoder'].encode(frame=frame, force_keyframe=is_keyframe) # convert to 1/900000
        except Exception as e:
            self.logger.error(f'Error packing frame of {topic}, data size size={len(im.data)}B: {e}')
            return
        
        if is_keyframe:
            sub['last_keyframe_stamp_ns'] = stamp_ns_raw
        try:
            frame_transport = marshal.dumps({
                'topic': topic,
                'frame_packets': packets,
                'ts': ts,
                'keyframe': is_keyframe,
            })
        except Exception as e:
            self.logger.error(f'Error packing frame of {topic}, data size size={len(im.data)}B: {e}')
            return
        
        self.out_queue_lock.acquire()
        try:
            self.out_queue.put_nowait(frame_transport)
        except Exception as e:
            self.logger.error(f'Error putting frame of {topic} into queue, data size size={len(im.data)}B: {e}')
        self.out_queue_lock.release()


    # software encoded compressed frames
    async def on_compressed_image_data(self, topic:str, msg:any):

        if not topic in self.active_subs.keys():
            return
        
        sub = self.active_subs[topic]
        
        if 'ignore' in sub:
            return
    
        try:
            im:CompressedImage = deserialize_message(msg, CompressedImage)
            size = len(im.data)
        except Exception as e:
            self.logger.error(f'Error deserializing compressed frame of {topic}, msg size={len(msg)}B: {e}')
            return

        if not sub['logged']:
            sub['logged'] = True
            self.logger.info(f'Processing compressed frame data for {topic}, format={im.format} size={size}B;')
            
        if size == 0:
            return
            
        if im.format == 'bgr8; jpeg compressed bgr8' or \
        im.format == 'rgb8; jpeg compressed bgr8':

            np_array = np.frombuffer(im.data, dtype=np.uint8) # Convert the image data to a NumPy array
            decoded = cv2.imdecode(np_array, cv2.IMREAD_COLOR) # bgr out    
            np_array = cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)
        
        # TODO: 16UC1 not tested as all cameras I have produce 0B data messages
        # elif im.format == '16UC1; jpeg compressed mono8':
        #     channels = 3  # 3 for RGB format
        #     np_array = np.frombuffer(im.data, dtype=np.uint16) # Convert the image data to a NumPy array
        #     decoded = cv2.imdecode(np_array, cv2.IMREAD_ANYDEPTH) # bgr in, bgr out
        #     h, w = decoded.shape[0:2]
        #     decoded = cv2.applyColorMap(decoded, cv2.COLORMAP_MAGMA)
        #     np_array = np_array.reshape(h, w, channels)
        
        else:
            self.logger.error(f'Received unsupported compressed frame data for {topic}, format="{im.format}" size={len(im.data)}B; not processing')
            sub['ignore'] = True
            return

        # software encode h264
        
        stamp_ns_raw = (im.header.stamp.sec * NS_TO_SEC) + (im.header.stamp.nanosec)
                
        frame = VideoFrame.from_ndarray(np_array, format="rgb24")
        frame.pts = stamp_ns_raw
        frame.time_base = fractions.Fraction(1, NS_TO_SEC) # will be converted to 1/90000

        is_keyframe = 'last_keyframe_stamp_ns' not in sub.keys() \
            or stamp_ns_raw - sub['last_keyframe_stamp_ns'] >= NS_TO_SEC # make first keyframe, then every second

        try:
            if 'encoder' not in sub.keys():
                sub['encoder'] = H264Encoder()
            packets, ts = sub['encoder'].encode(frame=frame, force_keyframe=is_keyframe) # convert to 1/900000
        except Exception as e:
            self.logger.error(f'Error packing compressed frame of {topic}, data size size={len(im.data)}B: {e}')
            return
        
        if is_keyframe:
            sub['last_keyframe_stamp_ns'] = stamp_ns_raw
        
        try:
            frame_transport = marshal.dumps({
                'topic': topic,
                'frame_packets': packets,
                'ts': ts,
                'keyframe': is_keyframe,
            })
        except Exception as e:
            self.logger.error(f'Error packing compressed frame of {topic}, data size size={len(im.data)}B: {e}')
            return
        
        self.out_queue_lock.acquire()
        try:
            self.out_queue.put_nowait(frame_transport)
        except Exception as e:
            self.logger.error(f'Error putting compressed frame of {topic} into queue, data size size={len(im.data)}B: {e}')
        self.out_queue_lock.release()
