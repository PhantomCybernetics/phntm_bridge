
from aiortc.contrib.media import MediaStreamTrack

import fractions
from typing import Tuple, Union
import time
import asyncio
import av
import time

from av.frame import Frame
from av.video.frame import VideoFrame
import numpy as np

from sensor_msgs.msg import Image
from rclpy.serialization import deserialize_message

from aiortc.rtcrtpsender import RTCEncodedFrame

AUDIO_PTIME = 0.020  # 20ms audio packetization
VIDEO_CLOCK_RATE = 90000
VIDEO_PTIME = 1 / 30  # 30fps
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)

class MediaStreamError(Exception):
    pass

import multiprocessing as mp
from queue import Empty, Full

# runs as a separate process for each subscribed image topic
def ROSFrameProcessor(topic:str, in_queue:mp.Queue, out_queue:mp.Queue):

    frame_num = 0
    _start = time.time()
    _timestamp = 0

    print(f'ROSFrameProcessor started for {topic}')

    try:
        while True:

            last_frame_bytes = None
            skipped = -1

            while True:
                fbytes = None
                try:
                    fbytes = in_queue.get(block=False)
                except Empty:
                    break
                skipped += 1
                last_frame_bytes = fbytes

            if last_frame_bytes == None:
                print(f'ROSFrameProcessor for {topic} empty')
                time.sleep(0.01)
                continue

            print(f'ROSFrameProcessor for {topic} got {len(last_frame_bytes)}, skipping {skipped} frames')

            frame_num += 1

    except Exception as e:
        print(f'ROSFrameProcessor finished for {topic} {str(e)}')

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


class ROSVideoStream(MediaStreamTrack):

    kind = "video"
    f:VideoFrame = None
    encodedFrame:RTCEncodedFrame = None
    # frame_msg_bytes = None

    # _start: float
    # _timestamp: int
    _logger = None
    _topic = None
    _id_peer = None
    _last_log_time = -1
    _log_message_every_sec = -1
    _total_received = 0
    _total_processed = 0

    def __init__(self, logger, topic, id_peer, log_message_every_sec) -> None:
        super().__init__()
        self._logger = logger
        self._topic = topic
        self._id_peer = id_peer
        self._log_message_every_sec = log_message_every_sec

    def get_logger(self):
        return self._logger

    def set_frame(self, frame:VideoFrame):
        # print(f'<< setting frame {frame.width}x{frame.height}')
        self.f = frame
        self._total_received += 1

    def set_encoded_frame(self, frame:RTCEncodedFrame):
        # print(f'<< setting frame {frame.width}x{frame.height}')

        self.f = None
        self.encodedFrame = frame

    # async def next_timestamp(self) -> Tuple[int, fractions.Fraction]:
    #     if hasattr(self, "_timestamp"):
    #         self._timestamp += int(VIDEO_PTIME * VIDEO_CLOCK_RATE)
    #         wait = self._start + (self._timestamp / VIDEO_CLOCK_RATE) - time.time()
    #         await asyncio.sleep(wait)
    #     else:
    #         self._start = time.time()
    #         self._timestamp = 0
    #     return self._timestamp, VIDEO_TIME_BASE

    async def recv(self) -> VideoFrame|RTCEncodedFrame:

        frame = None
        if self.encodedFrame:
            frame = self.encodedFrame
        else:
            frame = self.f

        self.f = None
        self.encodedFrame = None

        self._total_processed += 1

        if frame and (self._last_log_time < 0 or time.time()-self._last_log_time > self._log_message_every_sec):
            self._last_log_time = time.time() #last logged now
            self.get_logger().debug(f'△ {self._topic} {frame.width}x{frame.height} peer={self._id_peer}, f:{self._total_processed}/{self._total_received}')

        return frame