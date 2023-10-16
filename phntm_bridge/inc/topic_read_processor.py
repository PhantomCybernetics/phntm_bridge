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
from multiprocessing.connection import Connection

from sensor_msgs.msg import Image
from rclpy.serialization import deserialize_message

from .ros_video_streaming import ImageTopicReadSubscription

import numpy as np
import cv2
from aiortc.codecs import H264Encoder
from aiortc.codecs.h264 import DEFAULT_BITRATE, MIN_BITRATE, MAX_BITRATE
from av.frame import Frame
from av.video.frame import VideoFrame
import fractions


encoder_h264:H264Encoder = None

# runs as a separate process for bette isolation and lower ctrl/cam latency
def TopicReadProcessor(running_shared:mp.Value, ctrl_queue:mp.Queue, data_out_queue:mp.Queue,
                        conf:any, log_message_every_sec:float=5.0):

    print('Topic Reader: starting')

    # rclpy.init()

    rcl_ctx = Context()
    rcl_ctx.init() # This must be done before any ROS nodes can be created.
    # rcl_cbg = MutuallyExclusiveCallbackGroup()
    rcl_executor = SingleThreadedExecutor(context=rcl_ctx)
    reader_node = Node("phntm_bridge_reader", context=rcl_ctx, enable_rosout=False)

    # rcl_executor.add_node(reader_node)
    # rcl_cbg.add_entity(reader_node)
    # rcl_cbg.add_entity(rcl_ctx)
    # rcl_cbg.add_entity(rcl_executor)

    reader_node.get_logger().set_level(rclpy.logging.LoggingSeverity.DEBUG)
    # reader_node.load_config(self.get_logger())

    try:

        asyncio.run(TopicReadProcessorLoop(reader_node, rcl_executor, running_shared, ctrl_queue, data_out_queue, conf,log_message_every_sec))

    except (asyncio.CancelledError, KeyboardInterrupt):
        print(c(f'Topic Reader: Shutting down', 'red'))
        pass
    except Exception as e:
        print(c(f'Topic Reader: Exception in processor loop:', 'red'))
        traceback.print_exception(e)

    # reader_node.destroy_node()
    rcl_executor.shutdown()

    print('Topic Reader: finished')


async def SpinNode(reader_node, rcl_executor, running_shared:mp.Value):

    reader_node.get_logger().warn(f'Topic Reader: Spining the node...')
    while running_shared.value > 0:
        rclpy.spin_once(reader_node, executor=rcl_executor, timeout_sec=0.1)
        # rclpy.spin_once(reader_node, executor=rcl_executor, timeout_sec=0.1)
        await asyncio.sleep(.01)

    reader_node.get_logger().warn(f'Topic Reader: Done spinning')


async def TopicReadProcessorLoop(reader_node, rcl_executor, running_shared:mp.Value, ctrl_queue:mp.Queue, data_out_queue:mp.Queue,
                        conf:any, log_message_every_sec:float=5.0):

    active_subs:dict[str:dict] = {}
    newest_messages_by_topic:dict[str:list] = {}

    asyncio.get_event_loop().create_task(SpinNode(reader_node, rcl_executor, running_shared))

    while running_shared.value > 0:
        # print(c(f'yellow! {reader_node}', 'yellow'))

        # rclpy.spin_once(reader_node, executor=rcl_executor, timeout_sec=0.1)

        #recieve cmd messages
        while True:
            try:
                ctrl_cmd = ctrl_queue.get(block=False)
                on_cmd(reader_node, ctrl_cmd, active_subs, newest_messages_by_topic)
            except Empty:
                break # all messages processed

        #dropping older data here
        for topic in newest_messages_by_topic.keys():
            if not topic in active_subs.keys():
                continue #old data for unsubscribed

            for msg in newest_messages_by_topic[topic]:
                # reader_node.get_logger().info(f'I can has message for {topic}')
                try:
                    if active_subs[topic]['args']['msg_type'] == ImageTopicReadSubscription.MSG_TYPE:

                        if 'processing_task' in active_subs[topic].keys() and not active_subs[topic]['processing_task'].done():
                            continue #skip this frame as the previous one hasn't been consumed yet
                        # reader_node.get_logger().info(f'Processing {topic}')
                        image_task = asyncio.get_event_loop().create_task(on_image_data(topic=topic, msg=msg, out_pipe=active_subs[topic]['pipe'], subscription=active_subs[topic]))
                        active_subs[topic]['processing_task'] = image_task

                    else:
                        # reader_node.get_logger().info(f'Pushing {topic}')
                        data_out_queue.put_nowait({
                            'topic': topic, 'msg': msg
                        }) #put in output queue
                except Full:
                    reader_node.get_logger().warn(f'Topic Reader: Output queue full, dropping {topic} msg')
                    pass
        newest_messages_by_topic.clear()
        await asyncio.sleep(.01)

    # spin_future.set_result(True)


def on_cmd(reader_node:Node, ctrl_cmd:dict, active_subs:dict[str:dict], newest_messages_by_topic:dict[str:list]):

    topic = ctrl_cmd['topic']

    # unsubscribe or clear before subscribing again
    if ctrl_cmd['action'] == 'unsubscribe' or topic in active_subs.keys():
        reader_node.get_logger().info(f'Topic Reader: Destroying local subscriber for {topic}')
        reader_node.destroy_subscription(active_subs[topic]['sub'])
        del active_subs[topic]

    if ctrl_cmd['action'] == 'subscribe':

        msg_type = ctrl_cmd['msg_type']
        reliability = ctrl_cmd['reliability']
        durability = ctrl_cmd['durability']
        # reader_node.get_logger().error(f'Topic Reader: {topic} raw lifespan={ctrl_cmd["lifespan"]}')
        if 'lifespan' in ctrl_cmd.keys():
            if ctrl_cmd['lifespan'] == -1:
                lifespan = Infinite
            else:
                lifespan = Duration(seconds=ctrl_cmd['lifespan'])
        else:
            lifespan = Duration(seconds=1)
        pipe = ctrl_cmd['pipe'] if 'pipe' in ctrl_cmd.keys() else None

        message_class = None
        try:
            message_class = get_message(msg_type)
        except:
            pass
        if message_class == None:
            reader_node.get_logger().error(f'Topic Reader: NOT subscribing to topic {topic}, msg class {msg_type} not loaded')
            return

        qosProfile = QoSProfile(
                        history=QoSHistoryPolicy.KEEP_LAST,
                        depth=1,
                        reliability=reliability,
                        durability=durability,
                        lifespan=lifespan
                        )
        reader_node.get_logger().warn(f'Topic Reader: Subscribing to topic {topic} {msg_type} reliability={reliability} durability={durability} lifespan={lifespan}')
        no_skip:bool = ctrl_cmd['no_skip'] if 'no_skip' in ctrl_cmd.keys() else False
        sub = reader_node.create_subscription(
                        msg_type=message_class,
                        topic=topic,
                        callback=lambda msg: save_newest_msg(topic, msg, newest_messages_by_topic, no_skip),
                        qos_profile=qosProfile,
                        raw=True,
                    )

        args = ctrl_cmd
        args.pop('action')
        if 'reliability' in args.keys():
            args.pop('reliability')
        if 'durability' in args.keys():
            args.pop('durability')
        if 'pipe' in args.keys():
            args.pop('pipe')

        active_subs[topic] = {
            'sub': sub,
            'args': args,
            'pipe': pipe
        }
        if not topic in active_subs.keys():
            reader_node.get_logger().error(f'Topic Reader: Failed subscribing to topic {topic}, msg class={msg_type}')

def save_newest_msg(topic:str, msg:any, newest_messages_by_topic:dict[str:list], no_skip:bool):
    # reader_node.get_logger().info(f' >> {msg_topic}, got {len(msg)} B')

    # print (f'{topic} has data, no_skip={no_skip}')

    if topic not in newest_messages_by_topic.keys():
        newest_messages_by_topic[topic] = []

    if no_skip:
        newest_messages_by_topic[topic].append(msg)
    else:
        newest_messages_by_topic[topic] = [ msg ]


async def on_image_data(topic:str, msg:any, out_pipe:Connection, subscription:dict):

    if 'ignore' in subscription:
        return

    # print(f'Topic reader got {len(msg)}B image data for {topic}w args: {str(subscription["args"])}')

    debug_fp_start = time.time()
    im:Image = deserialize_message(msg, Image)

    if im.encoding == 'rgb8':
        channels = 3  # 3 for RGB format
        # Convert the image data to a NumPy array
        np_array = np.frombuffer(im.data, dtype=np.uint8)
        # Reshape the array based on the image dimensions
        np_array = np_array.reshape(im.height, im.width, channels)
    elif im.encoding == '16UC1':
        # channels = 1  # 3 for RGB format
        np_array = np.frombuffer(im.data, dtype=np.uint16) * float(255.0/4000.0)

        np_array = np.uint8 (np_array)

        # mask = np.zeros(np_array.shape, dtype=np.uint8)
        # mask = np.bitwise_or(np_array, mask)

        # np_array = cv2.cvtColor(np_array, cv2.COLOR_GRAY2RGB)
        np_array = cv2.applyColorMap(np_array, cv2.COLORMAP_MAGMA)

        # np_array = cv2.convertScaleAbs(np_array, alpha=255/2000) # converts to 8 bit
        # mask = cv2.convertScaleAbs(mask) # converts to 8 bit

        np_array = np_array.reshape(im.height, im.width, 3)
        # mask = mask.reshape(im.height, im.width, 1)

        # np_array = (255-np_array)
        # np_array = np.bitwise_and(np_array, mask)
    elif im.encoding == '32FC1':
        # channels = 1  # 3 for RGB format
        np_array = np.frombuffer(im.data, dtype=np.float32) * (255.0 * (1.0 / 2.0)) #;).astype(np.uint16)

        np_array = np.uint8 (np_array)

        # mask = np.ones(np_array.shape, dtype=np.uint8)
        # mask = np.bitwise_or(np_array, mask)

        # np_array = np_array * (255.0 / 2000.0)
        np_array = cv2.applyColorMap(np_array, cv2.COLORMAP_PLASMA)

        # np_array = cv2.cvtColor(np_array, cv2.COLOR_GRAY2RGB) #still

        #np_array = cv2.convertScaleAbs(np_array, alpha=1.0) # converts to 8 bit
        #
        # mask = convertScaleAbsmask) # converts to 8 bit

        np_array = np_array.reshape(im.height, im.width, 3)
        # mask = mask.reshape(im.height, im.width, 1)

        # np_array = (255-np_array)
        # np_array = np.bitwise_and(np_array, mask)
    else:
        print(f'Topic Reader: {topic} received unsupported frame type: F{im.header.stamp.sec}:{im.header.stamp.nanosec} {im.width}x{im.height} data={len(im.data)}B enc={im.encoding} is_bigendian={im.is_bigendian} step={im.step}, not processing')
        subscription['ignore'] = True
        return

    NS_TO_SEC = 1000000000
    # software encode h264
    frame = VideoFrame.from_ndarray(np_array, format="rgb24")

    stamp_ns_raw = int(im.header.stamp.sec*NS_TO_SEC) + int(im.header.stamp.nanosec)

    if not 'first_frame_time_ns' in subscription.keys():
        subscription['first_frame_time_ns'] = stamp_ns_raw
        subscription['last_frame_time_ns'] = stamp_ns_raw

    since_last_frame = stamp_ns_raw - subscription['last_frame_time_ns']
    subscription['last_frame_time_ns'] = stamp_ns_raw

    stamp_ns = stamp_ns_raw - subscription['first_frame_time_ns']

    frame.pts = stamp_ns
    frame.time_base = fractions.Fraction(1, NS_TO_SEC)

    # at around 5 FPS sw encoding is so slow it's actually better to send every frame as a keyframe
    force_keyframe = True

    # force_keyframe = 'last_keyframe_stamp_ns' not in subscription.keys() \
    #     or stamp_ns - subscription['last_keyframe_stamp_ns'] >= NS_TO_SEC #keyframe every second
    # if force_keyframe:
    #     subscription['last_keyframe_stamp_ns'] = stamp_ns

    global encoder_h264
    if encoder_h264 == None:
        encoder_h264 = H264Encoder()

    packets, timestamp = encoder_h264.encode(frame=frame, force_keyframe=force_keyframe) # convert to 1/90000

    # print(f'Processor {topic} stamp_ns={stamp_ns} raw={stamp_ns_raw} [{im.header.stamp.sec}:{im.header.stamp.nanosec}] dF={since_last_frame} f0={subscription["first_frame_time_ns"]} 1/90000={timestamp} KF={force_keyframe}')

    # _fp_4 = time.time()
    # _log_processed += 1
    # _log_cum_time += time.time() - debug_fp_start


    # if keyframe or _last_log_time < 0 or time.time()-_last_log_time > log_message_every_sec:
    #     _last_log_time = time.time() #last logged now
    #     # debug_times = f'Total: {"{:.5f}".format(_fp_4-_fp_start)}s\nIM: {"{:.5f}".format(_fp_1-_fp_start)}s\nConv: {"{:.5f}".format(_fp_2-_fp_1)}s\nVideoFrame {"{:.5f}".format(_fp_3-_fp_2)}s\nH264: {"{:.5f}".format(_fp_4-_fp_3)}s'
    #     debug_times = f'{_log_processed} in avg {"{:.5f}".format(_log_cum_time/_log_processed)}s'
    #     logger.info(f'[FP {topic}] {im.encoding}>H264 {im.width}x{im.height} {len(fbytes)}B > {len(packet)} pkts ' + (colored(' [KF]', 'magenta') if keyframe else '') + ' '+c(debug_times, 'yellow'))
    #     _log_processed = 0;
    #     _log_cum_time = 0.0

    # debug_times = f'{_log_processed} in avg {"{:.5f}".format(_log_cum_time/_log_processed)}s'

    # print(f'Topic Reader: processed frame of {topic} {im.encoding}>H264 {im.width}x{im.height} {len(msg)}B > {len(packets)} pkts' + (c(' [KF]', 'magenta') if keyframe else f' {ns_since_last_keyframe} ns since KF') + ' in '+c(time.time()-debug_fp_start, 'yellow'))

    try:
        await asyncio.get_event_loop().run_in_executor(None, out_pipe.send, { # blocks intil read and no processing of the same topic takes place
            'topic': topic,
            'frame_packets': packets,
            'timestamp': timestamp,
            'keyframe': force_keyframe, #don't skip keyframes
        }) #blocks

        # chill a bit while skipping frames of this topic
        # this affects fps of the output
        await asyncio.sleep(0.1)

    except Full:
        print(c(f'Topic Reader: output queue full (writing {topic})', 'red'))



