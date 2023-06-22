
from aiortc.contrib.media import MediaStreamTrack
from av.frame import Frame
from av.video.frame import VideoFrame
import fractions
from typing import Tuple, Union
import time
import asyncio
import av

import numpy as np

from sensor_msgs.msg import Image
from rclpy.serialization import deserialize_message

AUDIO_PTIME = 0.020  # 20ms audio packetization
VIDEO_CLOCK_RATE = 90000
VIDEO_PTIME = 1 / 30  # 30fps
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)

class MediaStreamError(Exception):
    pass

class ROSVideoStream(MediaStreamTrack):

    kind = "video"
    # f:VideoFrame = None
    frame_msg_bytes = None

    _start: float
    _timestamp: int
    _logger = None

    def __init__(self, logger) -> None:
        super().__init__()
        self._logger = logger

    def get_logger(self):
        return self._logger

    async def set_encoded_frame(self, msg_bytes:bytes):
        # print(f'<< setting frame {frame.width}x{frame.height}')
        self.frame_msg_bytes = msg_bytes

    async def next_timestamp(self) -> Tuple[int, fractions.Fraction]:
        if self.readyState != "live":
            self.get_logger().warn(f'self.readyState={self.readyState}')
            raise MediaStreamError

        if hasattr(self, "_timestamp"):
            self._timestamp += int(VIDEO_PTIME * VIDEO_CLOCK_RATE)
            wait = self._start + (self._timestamp / VIDEO_CLOCK_RATE) - time.time()
            await asyncio.sleep(wait)
        else:
            self._start = time.time()
            self._timestamp = 0
        return self._timestamp, VIDEO_TIME_BASE

    async def recv(self) -> VideoFrame:

        if not self.frame_msg_bytes:
            self.get_logger().info(f' >> no frame data to return')
            return None # kills the encoder??

        self.get_logger().warn(f' >> returning video frame form {len(self.frame_msg_bytes)}B')

        im:Image = deserialize_message(self.frame_msg_bytes, Image)
        self.frame_msg_bytes = None # don't encode again

        self.get_logger().warn(f' >> {str(im.header)} {im.width}x{im.height} data={len(im.data)}B enc={im.encoding} is_bigendian={im.is_bigendian} step={im.step}')

        if im.encoding == 'rgb8':
            channels = 3  # 3 for RGB format
            # Convert the image data to a NumPy array
            np_array = np.frombuffer(im.data, dtype=np.uint8)
            # Reshape the array based on the image dimensions
            np_array = np_array.reshape(im.height, im.width, channels)
            format = 'rgb24'
        elif im.encoding == '':
            pass

        frame = av.VideoFrame.from_ndarray(np_array, format=format) # im.encoding=rgb8 for /camera/color (rgb8 throws  exception=ValueError('Expected numpy array with ndim `2` but got `3`')>)

        pts, time_base = await self.next_timestamp()
        frame.pts = pts
        frame.time_base = time_base
        return frame