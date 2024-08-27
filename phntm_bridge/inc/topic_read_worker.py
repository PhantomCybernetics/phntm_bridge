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

encoder_h264:H264Encoder = None

# runs as a separate process for bette isolation and lower ctrl/cam latency
def TopicReadWorker(running_shared:mp.Value, reader_label:str, ctrl_queue:mp.Queue, 
                        conf:any, log_message_every_sec:float=5.0):

    print(f'Topic Reader {reader_label}: starting')
    
    # rclpy.init()

    rcl_ctx = Context()
    rcl_ctx.init() # This must be done before any ROS nodes can be created.
    # rcl_cbg = MutuallyExclusiveCallbackGroup()
    rcl_executor = SingleThreadedExecutor(context=rcl_ctx)
    reader_node = Node(node_name=f'phntm_worker_{reader_label}',
                       context=rcl_ctx,
                       enable_rosout=False,
                       use_global_arguments=False)

    # rcl_executor.add_node(reader_node)
    # rcl_cbg.add_entity(reader_node)
    # rcl_cbg.add_entity(rcl_ctx)
    # rcl_cbg.add_entity(rcl_executor)

    reader_node.get_logger().set_level(rclpy.logging.LoggingSeverity.DEBUG)

    # reader_node.load_config(self.get_logger())

    w = Worker(reader_node, reader_label, rcl_executor, running_shared, ctrl_queue)
    try:
        asyncio.run(w.worker_loop())
    except (asyncio.CancelledError, KeyboardInterrupt):
        reader_node.get_logger().info(c(f'Shutting down', 'red'))
        pass
    except Exception as e:
        reader_node.get_logger().error(f'Exception in worker loop:', e)

    reader_node.get_logger().warn(f'Stopping')

    # reader_node.destroy_node()
    rcl_executor.shutdown()
    

class Worker:
    
    def __init__(self, reader_node, reader_label, rcl_executor, running_shared, ctrl_queue) -> None:
        self.reader_node = reader_node
        self.reader_label = reader_label
        self.rcl_executor = rcl_executor
        self.running_shared = running_shared
        self.ctrl_queue = ctrl_queue
        self.logger = self.reader_node.get_logger()
        self.active_subs:dict[str:dict] = {}
        self.newest_messages_by_topic:dict[str:list] = {}


    async def spin_node_loop(self):

        self.logger.info(f'Spining the node...')
        while self.running_shared.value > 0:
            rclpy.spin_once(self.reader_node,
                            executor=self.rcl_executor,
                            timeout_sec=0.1)
            # rclpy.spin_once(reader_node, executor=rcl_executor, timeout_sec=0.1)
            await asyncio.sleep(.001)

        self.logger.info(f'Done spinning node')


    async def worker_loop(self):
        
        asyncio.get_event_loop().create_task(self.spin_node_loop())
        # spin_future = asyncio.Future()
        # asyncio.get_event_loop().run_in_executor(None, lambda: rclpy.spin_until_future_complete(reader_node, spin_future, executor=rcl_executor, timeout_sec=0.1))

        while self.running_shared.value > 0:
            # print(c(f'yellow! {reader_node}', 'yellow'))

            # rclpy.spin_once(self.reader_node, executor=self.rcl_executor, timeout_sec=0.1)

            #recieve cmd messages
            while True:
                try:
                    ctrl_cmd = self.ctrl_queue.get(block=False)
                    self.on_cmd(ctrl_cmd)
                except Empty:
                    break # all messages processed

            #dropping older data here
            # TODO THIS NEEDS TO MIX TOPICS MORE!
            # for topic in newest_messages_by_topic.keys():
            #     if not topic in active_subs.keys():
            #         continue #old data for unsubscribed

            #     for msg in newest_messages_by_topic[topic]:
            #         # reader_node.get_logger().info(f'I can has message for {topic}')
            #         try:
            #             if active_subs[topic]['args']['msg_type'] == ImageTopicReadSubscription.MSG_TYPE:

            #                 if 'processing_task' in active_subs[topic].keys() and not active_subs[topic]['processing_task'].done():
            #                     continue #skip this frame as the previous one hasn't been consumed yet
            #                 # reader_node.get_logger().info(f'Processing {topic}')
            #                 image_task = asyncio.get_event_loop().create_task(on_image_data(topic=topic, msg=msg, out_pipe=active_subs[topic]['pipe'], subscription=active_subs[topic], image_push_tasks=image_push_tasks))
            #                 active_subs[topic]['processing_task'] = image_task

            #             else:
            #                 # reader_node.get_logger().info(f'Pushing {topic}')
            #                 await on_data(topic=topic, msg=msg, out_pipe=active_subs[topic]['pipe'], subscription=active_subs[topic], data_push_tasks=data_push_tasks)
            #                 # active_subs[topic]['pipe'].
            #                 # data_out_queue.put_nowait({
            #                 #     'topic': topic, 'msg': msg
            #                 # }) #put in output queue
            #         except Full:
            #             reader_node.get_logger().warn(f'Topic Reader: Output queue full, dropping {topic} msg')
            #             pass
            # newest_messages_by_topic.clear()
            await asyncio.sleep(.001)

    def on_cmd(self, ctrl_cmd:dict):

        topic = ctrl_cmd['topic']

        # unsubscribe or clear before subscribing again
        if ctrl_cmd['action'] == 'unsubscribe' or topic in self.active_subs.keys():
            self.logger.info(f'Removing local subscriber for {topic}')
            if topic in self.active_subs.keys():
                self.active_subs[topic]['ignore'] = True # ignore any new data
                self.reader_node.destroy_subscription(self.active_subs[topic]['sub'])
                if 'push_task' in self.active_subs[topic].keys() and self.active_subs[topic]['push_task'] and not self.active_subs[topic]['push_task'].done():
                    self.logger.info(f'Cancelling unfinished push task for {topic}')
                    self.active_subs[topic]['push_task'].cancel()
                if 'pipe' in self.active_subs[topic].keys():
                    self.logger.info(f'Closing pipe for {topic}')
                    self.active_subs[topic]['push_task'] = asyncio.get_event_loop().run_in_executor(self.active_subs[topic]['executor'], self.active_subs[topic]['pipe'].send, {
                        'topic': topic,
                        'msg': None # = closing
                    }) # blocks until read
                    self.active_subs[topic]['push_task'].add_done_callback(lambda f: self.pipe_close(f, topic))

        if ctrl_cmd['action'] == 'subscribe':
            msg_type = ctrl_cmd['msg_type']
            reliability = QoSReliabilityPolicy(ctrl_cmd['reliability'])
            durability = DurabilityPolicy(ctrl_cmd['durability'])
            # reader_node.get_logger().error(f'Topic Reader: {topic} raw lifespan={ctrl_cmd["lifespan"]}')
            lifespan_hr = ''
            if not 'lifespan' in ctrl_cmd or ctrl_cmd['lifespan'] < 0:
                lifespan = Infinite
                lifespan_hr = 'Infinite'
            else:
                lifespan = Duration(seconds=ctrl_cmd['lifespan'])
                lifespan_hr = f'{lifespan.nanoseconds/S_TO_NS}s'

            pipe = ctrl_cmd['pipe'] if 'pipe' in ctrl_cmd.keys() else None

            message_class = None
            try:
                message_class = get_message(msg_type)
            except:
                pass
            if message_class == None:
                self.logger.error(f'NOT subscribing to topic {topic}, msg class {msg_type} not loaded')
                return

            try:
                qosProfile = QoSProfile(
                                history=QoSHistoryPolicy.KEEP_LAST,
                                depth=1,
                                reliability=reliability,
                                durability=durability,
                                lifespan=lifespan
                                )
                self.logger.warn(c(f'Subscribing to topic {topic} {msg_type} qosProfile={qosProfile} lifespan={lifespan_hr}', 'cyan'))
                no_skip:bool = ctrl_cmd['no_skip'] if 'no_skip' in ctrl_cmd.keys() else False
                
                cb = None
                if msg_type == ImageTopicReadSubscription.MSG_TYPE:
                    cb = lambda msg: self.on_raw_image_data(topic, msg)
                elif msg_type == ImageTopicReadSubscription.COMPRESSED_MSG_TYPE:
                    cb = lambda msg: self.on_compressed_image_data(topic, msg)
                elif msg_type == ImageTopicReadSubscription.STREAM_MSG_TYPE:
                    cb = lambda msg: self.on_stream_image_data(topic, msg)
                else:
                    cb = lambda msg: self.on_data(topic, msg)
                
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
                args.pop('action')
                if 'reliability' in args.keys():
                    args.pop('reliability')
                if 'durability' in args.keys():
                    args.pop('durability')
                if 'pipe' in args.keys():
                    args.pop('pipe')

                self.active_subs[topic] = {
                    'sub': sub,
                    'args': args,
                    'pipe': pipe,
                    'push_task': None,
                    'logged': False,
                    'executor': concurrent.futures.ThreadPoolExecutor(max_workers=1)
                }
                   
                
            except Exception as e:
                self.logger.error(f'Failed subscribing to topic {topic}, msg class={msg_type}: {e}')


    def pipe_error_catcher(self, f, topic):
        try:
            e = f.exception()
            if e:
                self.logger.error(f'Pipe output err {topic}: {e}')
        except: pass
    
    def pipe_close(self, f, topic):
        try:
            e = f.exception()
            if e:
                self.logger.error(f'Pipe close {topic} err: {e}')
            self.active_subs[topic]['pipe'].close()
            del self.active_subs[topic]
        except Exception as ee:
            self.logger.error(f'Pipe close {topic} exception {topic}: {ee}')


# def save_newest_msg(topic:str, msg:any, newest_messages_by_topic:dict[str:list], no_skip:bool, pipe:Connection):
#     # reader_node.get_logger().info(f' >> {msg_topic}, got {len(msg)} B')

#     # print (f'{topic} has data, no_skip={no_skip}')

#     if topic not in newest_messages_by_topic.keys():
#         newest_messages_by_topic[topic] = []

#     if no_skip:
#         newest_messages_by_topic[topic].append(msg)
#     else:
#         newest_messages_by_topic[topic] = [ msg ]


    def on_data(self, topic:str, msg:any):

        if not topic in self.active_subs.keys():
            return
        
        sub = self.active_subs[topic]
        
        if 'ignore' in sub:
            return
        
        if sub['push_task'] and not sub['push_task'].done():
            # print(f'Processsor skipping frame of {topic}, last not yet consumed yet')
            return
        
        sub['push_task'] = asyncio.get_event_loop().run_in_executor(sub['executor'], sub['pipe'].send, {
            'topic': topic,
            'msg': msg
        }) # blocks until read, no more frames of this topic are processed until then
        sub['push_task'].add_done_callback(lambda f: self.pipe_error_catcher(f, topic))


    def on_raw_image_data(self, topic:str, msg:any):

        if not topic in self.active_subs.keys():
            return
        
        sub = self.active_subs[topic]
        
        if 'ignore' in sub:
            return
        
        if sub['push_task'] and not sub['push_task'].done(): # skipping frames here
            return

        im:Image = deserialize_message(msg, Image)
        size = len(im.data)
        
        if not sub['logged']:
            sub['logged'] = True
            print(c(f'Processing raw frame data for {topic}, encoding={im.encoding} size={size}B; ', 'cyan'))

        if size == 0:
            return

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
            np_array = np.frombuffer(im.data, dtype=np.uint16) * float(255.0/4000.0)
            np_array = np.uint8 (np_array)
            np_array = cv2.applyColorMap(np_array, cv2.COLORMAP_MAGMA)
            np_array = np_array.reshape(im.height, im.width, 3)
        elif im.encoding == '32FC1': # channels = 1  # 3 for RGB format
            np_array = np.frombuffer(im.data, dtype=np.float32) * (255.0 * (1.0 / 2.0)) #;).astype(np.uint16)
            np_array = np.uint8 (np_array)
            np_array = cv2.applyColorMap(np_array, cv2.COLORMAP_PLASMA)
            np_array = np_array.reshape(im.height, im.width, 3)
        else:
            print(c(f'Received unsupported frame encoding for {topic}:  enc="{im.encoding}" is_bigendian={im.is_bigendian} {im.width}x{im.height} data={len(im.data)}B step={im.step}, not processing', 'red'))
            sub['ignore'] = True
            return

        NS_TO_SEC = 1000000000
        # software encode h264
        frame = VideoFrame.from_ndarray(np_array, format="rgb24")

        stamp_ns_raw = int(im.header.stamp.sec*NS_TO_SEC) + int(im.header.stamp.nanosec)

        if not 'first_frame_time_ns' in sub.keys():
            sub['first_frame_time_ns'] = stamp_ns_raw
            sub['last_frame_time_ns'] = stamp_ns_raw

        since_last_frame = stamp_ns_raw - sub['last_frame_time_ns']
        sub['last_frame_time_ns'] = stamp_ns_raw

        stamp_ns = stamp_ns_raw - sub['first_frame_time_ns']

        frame.pts = stamp_ns
        frame.time_base = fractions.Fraction(1, NS_TO_SEC)

        # at around 5 FPS sw encoding is so slow it's actually better to send every frame as a keyframe
        force_keyframe = False
        force_keyframe = 'last_keyframe_stamp_ns' not in sub.keys() \
            or stamp_ns - sub['last_keyframe_stamp_ns'] >= NS_TO_SEC #keyframe every second
        if force_keyframe:
            sub['last_keyframe_stamp_ns'] = stamp_ns

        global encoder_h264
        if encoder_h264 == None:
            encoder_h264 = H264Encoder()

        packets, timestamp = encoder_h264.encode(frame=frame, force_keyframe=force_keyframe) # convert to 1/90000

        # print(f'Processor {topic} stamp_ns={stamp_ns} raw={stamp_ns_raw} [{im.header.stamp.sec}:{im.header.stamp.nanosec}] dF={since_last_frame} f0={sub["first_frame_time_ns"]} 1/90000={timestamp} KF={force_keyframe}')

        # # _fp_4 = time.time()
        # # _log_processed += 1
        # # _log_cum_time += time.time() - debug_fp_start

        # # if keyframe or _last_log_time < 0 or time.time()-_last_log_time > log_message_every_sec:
        # #     _last_log_time = time.time() #last logged now
        # #     # debug_times = f'Total: {"{:.5f}".format(_fp_4-_fp_start)}s\nIM: {"{:.5f}".format(_fp_1-_fp_start)}s\nConv: {"{:.5f}".format(_fp_2-_fp_1)}s\nVideoFrame {"{:.5f}".format(_fp_3-_fp_2)}s\nH264: {"{:.5f}".format(_fp_4-_fp_3)}s'
        # #     debug_times = f'{_log_processed} in avg {"{:.5f}".format(_log_cum_time/_log_processed)}s'
        # #     logger.info(f'[FP {topic}] {im.encoding}>H264 {im.width}x{im.height} {len(fbytes)}B > {len(packet)} pkts ' + (colored(' [KF]', 'magenta') if keyframe else '') + ' '+c(debug_times, 'yellow'))
        # #     _log_processed = 0;
        # #     _log_cum_time = 0.0

        # # debug_times = f'{_log_processed} in avg {"{:.5f}".format(_log_cum_time/_log_processed)}s'

        # # print(f'Topic Reader: processed frame of {topic} {im.encoding}>H264 {im.width}x{im.height} {len(msg)}B > {len(packets)} pkts' + (c(' [KF]', 'magenta') if keyframe else f' {ns_since_last_keyframe} ns since KF') + ' in '+c(time.time()-debug_fp_start, 'yellow'))

        sub['push_task'] = asyncio.get_event_loop().run_in_executor(sub['executor'], sub['pipe'].send, {
            'topic': topic,
            'frame_packets': packets,
            'timestamp': timestamp,
            'keyframe': force_keyframe, # don't skip keyframes
        }) # blocks until read, no more frames of this topic are processed until then
        sub['push_task'].add_done_callback(lambda f: self.pipe_error_catcher(f, topic))


    def on_stream_image_data(self, topic:str, msg:any):
        
        if not topic in self.active_subs.keys():
            return
        
        sub = self.active_subs[topic]
        
        if 'ignore' in sub:
            return
        
        frame:FFMPEGPacket = deserialize_message(msg, FFMPEGPacket)
        size = len(frame.data)
        
        if frame.encoding != 'h.264':
            print(c(f'Received unsupported stream frame data for {topic}, format={frame.encoding} size={len(frame.data)}B; not processing', 'red'))
            sub['ignore'] = True
            return
        
        if not sub['logged']:
            sub['logged'] = True
            print(c(f'Processing stream frame data for {topic}, encoding={frame.encoding} size={size}B; ', 'cyan'))
            
        if size == 0:
            return
        
        if not 'first_frame_time_ns' in sub.keys():
            sub['first_frame_time_ns'] = frame.pts
            sub['last_frame_time_ns'] = frame.pts
        
        sub['last_frame_time_ns'] = frame.pts
        
        stamp_ns = frame.pts - sub['first_frame_time_ns']
        
        # we expect fully encoded frames here and only need to packetize them for transport
        p = Packet(frame.data)
        p.pts = stamp_ns
        p.time_base = fractions.Fraction(1, 90000)
        
        global encoder_h264
        if encoder_h264 == None:
            encoder_h264 = H264Encoder()
            
        packets, ts =  encoder_h264.pack(p)
        
        t = asyncio.get_event_loop().run_in_executor(sub['executor'], sub['pipe'].send, {
            'topic': topic,
            'frame_packets': packets,
            'timestamp': ts,
            'keyframe': frame.flags == 1, # don't skip keyframes
        })
        t.add_done_callback(lambda f: self.pipe_error_catcher(f, topic))


    def on_compressed_image_data(self, topic:str, msg:any):

        if not topic in self.active_subs.keys():
            return
        
        sub = self.active_subs[topic]
        
        if 'ignore' in sub:
            return
        
        if sub['push_task'] and not sub['push_task'].done(): # skipping frames here
            return

        im:CompressedImage = deserialize_message(msg, CompressedImage)
        size = len(im.data)

        if not sub['logged']:
            sub['logged'] = True
            print(c(f'Processing compressed frame data for {topic}, format={im.format} size={size}B; ', 'cyan'))
            
        if size == 0:
            return
            
        # if im.encoding == 'rgb8':
        #     channels = 3  # 3 for RGB format
        #     np_array = np.frombuffer(im.data, dtype=np.uint8) # Convert the image data to a NumPy array
        #     np_array = np_array.reshape(im.height, im.width, channels) # Reshape the array based on the image dimensions
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
            print(c(f'Received unsupported compressed frame data for {topic}, format="{im.format}" size={len(im.data)}B; not processing', 'red'))
            sub['ignore'] = True
            return

        NS_TO_SEC = 1000000000
        # software encode h264
        frame = VideoFrame.from_ndarray(np_array, format="rgb24")

        stamp_ns_raw = int(im.header.stamp.sec*NS_TO_SEC) + int(im.header.stamp.nanosec)

        if not 'first_frame_time_ns' in sub.keys():
            sub['first_frame_time_ns'] = stamp_ns_raw
            sub['last_frame_time_ns'] = stamp_ns_raw

        since_last_frame = stamp_ns_raw - sub['last_frame_time_ns']
        sub['last_frame_time_ns'] = stamp_ns_raw

        stamp_ns = stamp_ns_raw - sub['first_frame_time_ns']

        frame.pts = stamp_ns
        frame.time_base = fractions.Fraction(1, NS_TO_SEC)

        # at around 5 FPS sw encoding is so slow it's actually better to send every frame as a keyframe
        force_keyframe = False
        force_keyframe = 'last_keyframe_stamp_ns' not in sub.keys() \
            or stamp_ns - sub['last_keyframe_stamp_ns'] >= NS_TO_SEC #keyframe every second
        if force_keyframe:
            sub['last_keyframe_stamp_ns'] = stamp_ns

        global encoder_h264
        if encoder_h264 == None:
            encoder_h264 = H264Encoder()

        packets, timestamp = encoder_h264.encode(frame=frame, force_keyframe=force_keyframe) # convert to 1/90000
        
        # print(f'Processor pushing image for {topic}')
        sub['push_task'] = asyncio.get_event_loop().run_in_executor(sub['executor'], sub['pipe'].send, {
            'topic': topic,
            'frame_packets': packets,
            'timestamp': timestamp,
            'keyframe': force_keyframe, # don't skip keyframes
        }) # blocks until read, no more frames of this topic are processed until then
        sub['push_task'].add_done_callback(lambda f: self.pipe_error_catcher(f, topic))

