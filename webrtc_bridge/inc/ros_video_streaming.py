
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