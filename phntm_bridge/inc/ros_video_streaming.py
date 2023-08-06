
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

import cv2
from aiortc.codecs import Vp8Encoder
from aiortc.codecs import H264Encoder

from aiortc.codecs.h264 import DEFAULT_BITRATE, MIN_BITRATE, MAX_BITRATE
DEFAULT_BITRATE = MAX_BITRATE

from termcolor import colored

from av.frame import Frame
from av.video.frame import VideoFrame
import numpy as np

from sensor_msgs.msg import Image
from rclpy.serialization import deserialize_message
from sensor_msgs.msg import Image
# from aiortc.rtcrtpsender import RTCEncodedFrame

# AUDIO_PTIME = 0.020  # 20ms audio packetization
VIDEO_CLOCK_RATE = 1000000000 #ns to s
# VIDEO_PTIME = 1 / 30  # 30fps
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)

class MediaStreamError(Exception):
    pass

import rclpy
from rclpy.node import Node, Parameter, Subscription, QoSProfile, Publisher

from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy
from rclpy.duration import Duration, Infinite

import multiprocessing as mp
from queue import Empty, Full

class ImageNode(Node):

    topic:str
    last_message:bytes = None
    subscription = None

    def __init__(self, image_topic:str, context:rclpy.context.Context, cbg:rclpy.callback_groups.CallbackGroup):
        super().__init__('phntm_bridge_img', context=context)
        self.topic = image_topic

        self.get_logger().info(f'[ImNode {self.topic}] subscribing to {image_topic}')

        qosProfile = QoSProfile(history=QoSHistoryPolicy.KEEP_LAST, \
                        depth=1, \
                        reliability=QoSReliabilityPolicy.BEST_EFFORT, \
                        durability=DurabilityPolicy.VOLATILE, \
                        lifespan=Infinite \
                        )

        self.subscription = self.create_subscription(
            msg_type=Image,
            topic=image_topic,
            callback=self.listener_callback,
            qos_profile=qosProfile,
            raw=True,
            callback_group=cbg
        )
        self.subscription  # prevent unused variable warning
        self.get_logger().info(f'[ImNode {self.topic}] init done')


    def listener_callback(self, msg):
        self.get_logger().info(f'[ImNode {self.topic}] for msg {len(msg)} B')
        self.last_message = msg



# runs as a separate process for each subscribed image topic
def ROSFrameProcessor(topic:str, out_queue_h264:mp.Queue, out_queue_v8:mp.Queue,
                      make_keyframe_shared:mp.Value, make_h264_shared:mp.Value, make_v8_shared:mp.Value,
                      logger:logging.Logger, log_message_every_sec:float=5.0):

    frame_num = 0
    _first_frame_time_ns = 0
    _last_log_time = -1

    _log_cum_time = 0.0
    _log_processed = 0

    logger.info(f'[FP {topic}] started')
    encoder_h264 = H264Encoder()

    # rclpy.init(signal_handler_options=rclpy.SignalHandlerOptions.NO)
    # rclpy.uninstall_signal_handlers() #duplicate?
    ctx = rclpy.context.Context()
    ctx.init()

    cbg = rclpy.callback_groups.MutuallyExclusiveCallbackGroup()

    executor = rclpy.executors.MultiThreadedExecutor(context=ctx)
    image_node = ImageNode(topic, context=ctx, cbg=cbg)
    executor.add_node(image_node)
    cbg.add_entity(image_node)
    cbg.add_entity(ctx)
    cbg.add_entity(executor)
    # cbg.beginning_execution(image_node)

    try:
        while True:

            # last_frame_data = None
            # skipped = -1

            # fbytes = pipe_in.recv_bytes()
            fbytes = None

            # rclpy.spin_once(image_node, executor=executor)
            # logger.info(f'ROSFrameProcessor {topic} spinning once...')
            executor.spin_once()

            fbytes = image_node.last_message
            image_node.last_message = None

            if fbytes is None:
                logger.error(f'ROSFrameProcessor for {topic} empty!')
                time.sleep(0.001)
                continue

            # logger.debug(f'ROSFrameProcessor for {topic} can haz frame!')

            # while True:
            #     try:
            #         fbytes = in_queue.get(block=False) #blocking on first, then throws on Empty
            #     except Empty:
            #         break
            #     skipped += 1
            #     # last_frame_data = fdata

            # if fbytes is None:
            #     # logger.error(f'ROSFrameProcessor for {topic} empty! skipped={skipped}')
            #     time.sleep(0.001)
            #     continue

            _fp_start = time.time()
            # _fp_start = time.time()

            frame_num += 1
            im:Image = deserialize_message(fbytes, Image)

            # _fp_1 = time.time()

            # if skipped > 0:
            #     logger.warn(f'[FP {topic}] skipped {skipped} frames')

            #logger.debug(f'[FP {topic}] {im.header.stamp.sec}:{im.header.stamp.nanosec}: {im.width}x{im.height}, {len(fbytes)}B, enc={im.encoding}' + (f'(skipped {skipped} frames)' if skipped > 0 else ''))

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
                np_array = cv2.applyColorMap(np_array, cv2.COLORMAP_MAGMA)

                # np_array = cv2.cvtColor(np_array, cv2.COLOR_GRAY2RGB) #still

                #np_array = cv2.convertScaleAbs(np_array, alpha=1.0) # converts to 8 bit
                #
                # mask = convertScaleAbsmask) # converts to 8 bit

                np_array = np_array.reshape(im.height, im.width, 3)
                # mask = mask.reshape(im.height, im.width, 1)

                # np_array = (255-np_array)
                # np_array = np.bitwise_and(np_array, mask)
            else:
                logger.error(f'[FP {topic}] Unsupported frame type: F{im.header.stamp.sec}:{im.header.stamp.nanosec} {im.width}x{im.height} data={len(im.data)}B enc={im.encoding} is_bigendian={im.is_bigendian} step={im.step}')
                logger.error(f'[FP {topic}] Frame processor stopped')
                break

            # pts, time_base = self.next_timestamp()

            # yuv420p = False # last_frame_data["yuv420p"]
            # rgb = True # last_frame_data["rgb"]

            # logger.info(f'[FP {topic}] sending to yuv420p:{str(yuv420p)}, rgb:{str(rgb)}')

            # _fp_2 = time.time()

            if make_v8_shared.value > 0: #vp8 uses this
                logger.error(f'[FP {topic}] NOT making v8 yet')
                pass
                # frame = av.VideoFrame.from_ndarray(np_array, format='rgb24')
                # out_queue_yuv420p.put_nowait(frame.reformat(format="yuv420p").to_ndarray())
            if make_h264_shared.value > 0:

                frame = av.VideoFrame.from_ndarray(np_array, format="rgb24")

                # _fp_3 = time.time()

                # stamp = time.time_ns() #(frame_data['stamp_sec']*VIDEO_CLOCK_RATE + frame_data['stamp_nanosec']) - self._first_frame_time
                stamp = (im.header.stamp.sec*VIDEO_CLOCK_RATE + im.header.stamp.nanosec) - _first_frame_time_ns
                if _first_frame_time_ns == 0:
                    _first_frame_time_ns = stamp # time.time_ns()
                    stamp = 0
                frame.pts = stamp
                frame.time_base = VIDEO_TIME_BASE

                keyframe = make_keyframe_shared.value > 0
                if keyframe:
                    make_keyframe_shared.value = 0 #reset
                packet, timestamp = encoder_h264.encode(frame=frame, force_keyframe=keyframe) # -> Tuple[List[bytes], int]

                # _fp_4 = time.time()
                _log_processed += 1
                _log_cum_time += time.time() - _fp_start

                if keyframe or _last_log_time < 0 or time.time()-_last_log_time > log_message_every_sec:
                    _last_log_time = time.time() #last logged now
                    # debug_times = f'Total: {"{:.5f}".format(_fp_4-_fp_start)}s\nIM: {"{:.5f}".format(_fp_1-_fp_start)}s\nConv: {"{:.5f}".format(_fp_2-_fp_1)}s\nVideoFrame {"{:.5f}".format(_fp_3-_fp_2)}s\nH264: {"{:.5f}".format(_fp_4-_fp_3)}s'
                    debug_times = f'{_log_processed} in avg {"{:.5f}".format(_log_cum_time/_log_processed)}s'
                    logger.info(f'[FP {topic}] {im.encoding}>H264 {im.width}x{im.height} {len(fbytes)}B > {len(packet)} pkts ' + (colored(' [KF]', 'magenta') if keyframe else '') + ' '+colored(debug_times, 'yellow'))
                    _log_processed = 0;
                    _log_cum_time = 0.0

                try:
                    out_queue_h264.put_nowait((
                        packet,
                        timestamp,
                        keyframe #don't skip keyframes
                    ))
                except Full:
                    logger.warn(f'[FP {topic}] h264 output queue full (reading slow)')

            time.sleep(0.01)

            #out_queue.put_nowait(resdata)

    #     # self.get_logger().info(f'Frame worker thread {topic} frame no:{frame_num} threads={active_count()}')

    #     receivers_yuv420p = []
    #     for id_peer in self.wrtc_peer_video_tracks.keys():
    #         if topic in self.wrtc_peer_video_tracks[id_peer].keys():
    #             if self.wrtc_peer_video_tracks[id_peer][topic].track != None:
    #                 sender = self.wrtc_peer_video_tracks[id_peer][topic]
    #                 if isinstance(sender.encoder, Vp8Encoder):
    #                     receivers_yuv420p.append(sender)
    #                 else:
    #                     sender.track.set_frame(frame)

    #     if len(receivers_yuv420p): # preprocess here for all subscribers
    #         frame_yuv420p = frame.reformat(format="yuv420p")
    #         for sender in receivers_yuv420p:
    #             sender.track.set_frame(frame_yuv420p)

    #     # self.topic_read_frame_enc_[topic][codec] = frame

    except Exception as e:
        logger.error(f'ROSFrameProcessor finished for {topic} {str(e)}\n{traceback.format_exc()}')

    image_node.destroy_node()
    executor.shutdown()


class ROSVideoStreamTrack(MediaStreamTrack):

    kind = "video"
    # f:VideoFrame = None
    # encodedFrame:RTCEncodedFrame = None
    # frame_msg_bytes = None
    _timestamp = 0

    # _start: float
    # _timestamp: int
    _logger = None
    _topic_read_subscriptions = None
    _topic = None
    _id_peer = None
    _last_log_time = -1
    _log_message_every_sec = -1
    _total_received = 0
    _total_processed = 0
    _sender = None

    send_queue:mp.Queue = mp.Queue()

    def __init__(self, logger, topic, topic_read_subscriptions, id_peer, log_message_every_sec) -> None:
        super().__init__()
        self._logger = logger
        self._topic = topic
        self._topic_read_subscriptions = topic_read_subscriptions
        self._id_peer = id_peer
        self._log_message_every_sec = log_message_every_sec
        # self._logger.error(f'All good  in {topic}, enc={str(self.encoder)}')

    def set_sender(self, sender):
        self._sender = sender

    def get_logger(self):
        return self._logger

    async def recv(self) -> Tuple[List[bytes], int]: #returning what the worker therad alerady encoded and packetized

        subs = self._topic_read_subscriptions[self._topic]

        q = None
        if isinstance(self._sender.encoder, Vp8Encoder):
            q = subs.processed_frames_v8
        else:
            q = subs.processed_frames_h264 #ONLY THIS WORKS for H264 for now

        packet_data = None
        last_kf_packet_data = None
        skipped = -1
        is_keyframe = False

        while True:
            try:
                packet_data = q.get(block=False)
                is_keyframe = packet_data[2]
                if is_keyframe:
                    last_kf_packet_data = packet_data
            except Empty:
                break
            skipped += 1

        if last_kf_packet_data != None:
            packet_data = last_kf_packet_data

        if packet_data is None:
            # self.get_logger().error(f'{self._topic} recv nothing to return (skipped={skipped})')
            return None

        if skipped > 0:
            self.get_logger().warn(f'{self._topic} recv skipped {skipped} frames')

        self._total_processed += 1

        if is_keyframe or self._last_log_time < 0 or time.time()-self._last_log_time > self._log_message_every_sec:
            self._last_log_time = time.time() #last logged now
            self.get_logger().debug(f'△ {self._topic} peer={self._id_peer}, f:{self._total_processed}/{self._total_received}')

        # self.get_logger().error(f'{self._topic} recv returning packet data')

        return packet_data # Tuple[List[bytes], int]