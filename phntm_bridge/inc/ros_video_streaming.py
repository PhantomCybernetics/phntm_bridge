
from aiortc.contrib.media import MediaStreamTrack

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

from termcolor import colored

from av.frame import Frame
from av.video.frame import VideoFrame
import numpy as np

from sensor_msgs.msg import Image
from rclpy.serialization import deserialize_message

from aiortc.rtcrtpsender import RTCEncodedFrame

# AUDIO_PTIME = 0.020  # 20ms audio packetization
VIDEO_CLOCK_RATE = 1000000000 #ns to s
# VIDEO_PTIME = 1 / 30  # 30fps
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)

class MediaStreamError(Exception):
    pass

import multiprocessing as mp
from queue import Empty, Full

# runs as a separate process for each subscribed image topic
def ROSFrameProcessor(topic:str, in_queue:mp.Queue, out_queue_rgb:mp.Queue, out_queue_yuv420p:mp.Queue, logger:logging.Logger):

    frame_num = 0
    _start = time.time()

    logger.info(f'[FP {topic}] started')

    try:
        while True:

            # last_frame_data = None
            skipped = -1

            # fbytes = pipe_in.recv_bytes()
            fbytes = None

            while True:
                try:
                    fbytes = in_queue.get(block=(skipped==-1)) #blocking on first, then throws on Empty
                except Empty:
                    break
                skipped += 1
                # last_frame_data = fdata

            if fbytes is None:
                logger.error(f'ROSFrameProcessor for {topic} empty! skipped={skipped}')
                break

            frame_num += 1
            im:Image = deserialize_message(fbytes, Image)

            if skipped > 0:
                logger.warn(f'[FP {topic}] skipped {skipped} frames')

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

            yuv420p = False # last_frame_data["yuv420p"]
            rgb = True # last_frame_data["rgb"]

            # logger.info(f'[FP {topic}] sending to yuv420p:{str(yuv420p)}, rgb:{str(rgb)}')

            if yuv420p: #vp8 uses this
                pass
                # frame = av.VideoFrame.from_ndarray(np_array, format='rgb24')
                # out_queue_yuv420p.put_nowait(frame.reformat(format="yuv420p").to_ndarray())
            if rgb:
                try:
                    out_queue_rgb.put_nowait({
                        'np_array': np_array,
                        'enc_in': im.encoding,
                        'stamp_sec': im.header.stamp.sec,
                        'stamp_nanosec': im.header.stamp.nanosec
                    })
                except Full:
                    logger.error(f'[FP {topic}] output queue full!')

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


    # self.get_logger().info(f'Frame worker thread {topic} started')

    # # vp8_encoder = Vp8Encoder()

    # while not self.shutting_down_ and topic in self.topic_read_subscriptions_.keys():

    #     _timestamp += int(VIDEO_PTIME * VIDEO_CLOCK_RATE)
    #     if not topic in self.topic_read_frame_raw_.keys() or not self.topic_read_frame_raw_[topic]:
    #         # self.get_logger().info(f' ... Frame worker thread {topic} input empty')
    #         time.sleep(.001)
    #         continue

    #     src_msg = self.topic_read_frame_raw_[topic]
    #     self.topic_read_frame_raw_[topic] = None # don't encode again

    #     im:Image = deserialize_message(src_msg, Image)

    #     # self.get_logger().warn(f' ... >> F{im.header.stamp.sec}:{im.header.stamp.nanosec} {im.width}x{im.height} data={len(im.data)}B enc={im.encoding} is_bigendian={im.is_bigendian} step={im.step}')

    #     if im.encoding == 'rgb8':
    #         channels = 3  # 3 for RGB format
    #         # Convert the image data to a NumPy array
    #         np_array = np.frombuffer(im.data, dtype=np.uint8)
    #         # Reshape the array based on the image dimensions
    #         np_array = np_array.reshape(im.height, im.width, channels)
    #         format = 'rgb24'
    #     elif im.encoding == '16UC1':
    #         # channels = 1  # 3 for RGB format
    #         np_array = np.frombuffer(im.data, dtype=np.uint16)

    #         mask = np.zeros(np_array.shape, dtype=np.uint8)
    #         mask = np.bitwise_or(np_array, mask)

    #         np_array = cv2.cvtColor(np_array, cv2.COLOR_GRAY2RGB)

    #         np_array = cv2.convertScaleAbs(np_array, alpha=255/2000) # converts to 8 bit
    #         mask = cv2.convertScaleAbs(mask) # converts to 8 bit

    #         np_array = np_array.reshape(im.height, im.width, 3)
    #         mask = mask.reshape(im.height, im.width, 1)

    #         np_array = (255-np_array)
    #         np_array = np.bitwise_and(np_array, mask)

    #         format = 'rgb24'
    #     else:
    #         self.get_logger().error(f' >> Unsupported frame type: F{im.header.stamp.sec}:{im.header.stamp.nanosec} {im.width}x{im.height} data={len(im.data)}B enc={im.encoding} is_bigendian={im.is_bigendian} step={im.step}')
    #         self.get_logger().error(f' >> Frame processor stopping for {topic}')
    #         break

    #     frame = av.VideoFrame.from_ndarray(np_array, format=format)

    #     # pts, time_base = self.next_timestamp()
    #     frame.pts = _timestamp
    #     frame.time_base = VIDEO_TIME_BASE
    #     frame_num += 1

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

    # self.get_logger().warn(f'Frame worker for {topic} finished')


class ROSVideoStreamTrack(MediaStreamTrack):

    kind = "video"
    # f:VideoFrame = None
    # encodedFrame:RTCEncodedFrame = None
    # frame_msg_bytes = None
    _timestamp = 0
    _first_frame_time = 0

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

    # def set_frame(self, frame:VideoFrame):
    #     # print(f'<< setting frame {frame.width}x{frame.height}')
    #     self.f = frame
    #     self._total_received += 1

    # def set_encoded_frame(self, frame:RTCEncodedFrame):
    #     # print(f'<< setting frame {frame.width}x{frame.height}')

    #     self.f = None
    #     self.encodedFrame = frame

    # async def next_timestamp(self) -> Tuple[int, fractions.Fraction]:
    #     if hasattr(self, "_timestamp"):
    #         self._timestamp += int(VIDEO_PTIME * VIDEO_CLOCK_RATE)
    #         wait = self._start + (self._timestamp / VIDEO_CLOCK_RATE) - time.time()
    #         await asyncio.sleep(wait)
    #     else:
    #         self._start = time.time()
    #         self._timestamp = 0
    #     return self._timestamp, VIDEO_TIME_BASE

    async def recv(self) -> VideoFrame|None:

        subs = self._topic_read_subscriptions[self._topic]

        q = None
        if isinstance(self._sender.encoder, Vp8Encoder):
            q = subs.processed_frames_yuv420p
        else:
            q = subs.processed_frames_rgb #ONLY THIS WORKS for H264 for now

        frame_data = None
        # last_frame_data = None
        skipped = -1

        while True:
            # fbytes = None
            # fbytes = None
            try:
                frame_data = q.get(block=False)
            except Empty:
                break
            skipped += 1
            # last_fbytes = fbytes

        # print(str(last_fbytes))
        if frame_data is None:
            # self.get_logger().error(f'{self._topic} recv nothing to return (skipped={skipped})')
            return None

        start_time = time.time()
        frame = av.VideoFrame.from_ndarray(frame_data['np_array'], format="rgb24")

        stamp = time.time_ns() #(frame_data['stamp_sec']*VIDEO_CLOCK_RATE + frame_data['stamp_nanosec']) - self._first_frame_time

        if self._first_frame_time == 0:
            self._first_frame_time = stamp # time.time_ns()

        frame.pts = stamp - self._first_frame_time

        # self.get_logger().info(f'{self._topic} recv sec:ns = {frame_data["stamp_sec"]}:{frame_data["stamp_nanosec"]} pts={frame.pts}')

        #     # self._timestamp = self._timestamp + int(VIDEO_PTIME * VIDEO_CLOCK_RATE)
        # else:
        #     frame.pts = 0
        frame.time_base = VIDEO_TIME_BASE

        if skipped > 0:
            self.get_logger().warn(f'{self._topic} recv skipped {skipped} frames')

        self._total_processed += 1

        if frame and (self._last_log_time < 0 or time.time()-self._last_log_time > self._log_message_every_sec):
            self._last_log_time = time.time() #last logged now
            self.get_logger().debug(f'△ {self._topic} {frame.width}x{frame.height} peer={self._id_peer}, f:{self._total_processed}/{self._total_received}')

        return frame