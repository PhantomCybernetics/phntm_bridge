
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput
from picamera2 import Picamera2
from aiortc.contrib.media import MediaStreamTrack
from typing import Tuple, Union, List
from termcolor import colored as c
import av
import fractions
import time

from rclpy.impl.rcutils_logger import RcutilsLogger

from picamera2.outputs import FileOutput
from picamera2 import Picamera2
import libcamera

from aiortc import RTCRtpSender
from aiortc.codecs.h264 import PACKET_MAX
import asyncio

NS_TO_SEC = 1000000000
# VIDEO_PTIME = 1 / 30  # 30fps
SRC_VIDEO_TIME_BASE = fractions.Fraction(1, NS_TO_SEC)

from aiortc.mediastreams import VIDEO_TIME_BASE, convert_timebase
from aiortc.codecs.h264 import H264Encoder as aiortcH264Encoder

def IsPiCameraId(id_topic_od_camera:str) -> bool:
    if id_topic_od_camera.startswith('/picam2'):
        return True
    return False

class Picamera2Subscription:

    def __init__(self, id_camera:str, picam2:Picamera2, logger:RcutilsLogger, bridge_time_started_ns:int, hflip:bool=False, vflip:bool=False, bitrate:int=5000000, framerate:int = 30, log_message_every_sec:float=5.0):
        self.id_camera:str = id_camera
        self.num_received:int = 0
        self.peers:dict[str:RTCRtpSender] = {}
        self.logger:RcutilsLogger = logger

        self.hflip = hflip
        self.vflip = vflip
        self.bitrate = bitrate
        self.framerate = framerate

        self.picam2:Picamera2 = picam2
        self.encoder:H264Encoder = None
        self.output:PacketsOutput = None
        self.event_loop = None

        self.bridge_time_started_ns:int = bridge_time_started_ns
        self.log_message_every_sec:float = log_message_every_sec

    async def start(self, id_peer:str, sender:RTCRtpSender) -> bool:

        if self.output != None:
            self.peers[id_peer] = sender
            return True #all done, one sub for all

        # preview_config = self.picam2.create_preview_configuration(display='main',
        #                                                         encode='main',
        #                                                         transform=libcamera.Transform(hflip=1, vflip=1),
        #                                                         queue=False
        #                                                         )

        self.event_loop = asyncio.get_event_loop()

        # video_config = self.picam2.create_preview_configuration(queue=False,
        #
        # full res has much lowerr latency (?)
        video_config = self.picam2.create_video_configuration(queue=False,
                                                              transform=libcamera.Transform(hflip=1 if self.hflip else 0,
                                                                                            vflip=1 if self.vflip else 0))
        self.picam2.configure(video_config)
        self.encoder = H264Encoder(bitrate=self.bitrate, framerate=self.framerate)
        self.output = PacketsOutput(sub=self, bridge_time_started_ns=self.bridge_time_started_ns, logger=self.logger, log_message_every_sec=self.log_message_every_sec)

        self.peers[id_peer] = sender
        # self.peers[id_peer].track.set_output(self.output)

        await asyncio.sleep(2.0) #camera setup time (here?)

        self.logger.info(c(f'Picam2 recording...', 'magenta'))

        # picam2.start_recording()
        self.picam2.start_encoder(encoder=self.encoder, output=self.output)
        self.picam2.start()

        return True

    def stop(self, id_peer:str):
        if id_peer in self.peers.keys():
            self.peers.pop(id_peer)

        if len(self.peers) == 0:
            self.logger.info(c(f'Picam stopping', 'magenta'))

            self.picam2.stop_encoder()
            self.picam2.stop()

            self.encoder = None
            self.output = None

            return True #destroyed
        else:
            return False


def get_camera_info(picam2:Picamera2) -> List[Tuple[str, dict]]:
    data = []
    info = picam2.global_camera_info()

    for c in info:
        cam_data = [
            f'/picam2{c["Id"]}', #our id
            c,
            # msg types follow
        ]
        data.append(cam_data)
    return data

def picam2_has_camera(picam2:Picamera2, id_cam:str) -> bool:

    available = get_camera_info(picam2)

    for cam in available:
        if cam[0] == id_cam:
            return True

    return False


#shared camera frame output
class PacketsOutput(FileOutput):

    # last_frame: = None
    # last_keyframe:av.Packet = None
    # last_timestamp = 0
    # last_keyframe_timestamp = 0
    # last_was_keyframe = False

    def __init__(self, sub:Picamera2Subscription, bridge_time_started_ns:int, logger:RcutilsLogger, log_message_every_sec:float=5.0):
        super().__init__()
        # self.last_frameav.Packet = None
        # self.last_keyframe = None
        # self.last_timestamp = 0
        # elf.last_keyframe_timestamp = 0
        # self.last_was_keyframe = False
        self.sub:Picamera2Subscription = sub
        self.last_frame:int = 0
        self.bridge_time_started_ns:int = bridge_time_started_ns
        self.num_received = 0
        self.logger:RcutilsLogger = logger

        self.last_log:float = -1.0
        self.log_message_every_sec:float = log_message_every_sec

        self.aiortc_encoder:aiortcH264Encoder() = aiortcH264Encoder()

    def outputframe(self, frame_bytes, keyframe=True, timestamp=None):
        """Outputs frame from encoder

        :param frame: Frame
        :type frame: bytes
        :param keyframe: Whether frame is a keyframe, defaults to True
        :type keyframe: bool, optional
        :param timestamp: Timestamp of frame
        :type timestamp: int
        """

        if not self.sub.event_loop.is_running:
            return

        self.num_received += 1

        packet = av.Packet(frame_bytes)
        packet.pts = timestamp # ns
        packet.time_base = SRC_VIDEO_TIME_BASE

        log_msg = False
        if self.num_received == 1: # first data in
            log_msg = True
            self.logger.debug(f'👁️  Receiving {len(frame_bytes)}B frame from camera {self.sub.id_camera}')

        if self.last_log < 0 or time.time()-self.last_log > self.log_message_every_sec:
            log_msg = True
            self.last_log = time.time() #last logged now

        payloads, stamp_converted = self.aiortc_encoder.pack(packet)

        self.last_frame = timestamp
        for id_peer in self.sub.peers.keys():
            # fut = self.sub.event_loop.create_future()
            if log_msg:
                self.logger.info(f'👁️  △ Sending {len(frame_bytes)}B / {len(payloads)} pkgs of {self.sub.id_camera} to id_peer={id_peer} / id_stream= {str(self.sub.peers[id_peer]._stream_id)}, total received: {self.num_received}, ts={timestamp}, sender={str(id(self.sub.peers[id_peer]))}')
            # if timestamp == 0:
                # offset_ns = time.time_ns()-self.bridge_time_started_ns
                # self.sub.peers[id_peer].timestamp_origin = convert_timebase(offset_ns, SRC_VIDEO_TIME_BASE, VIDEO_TIME_BASE)
            self.sub.event_loop.create_task(self.sub.peers[id_peer].send_direct(frame_data=payloads, stamp_converted=stamp_converted, keyframe=keyframe))

        # if self.recording:
        #     if self._firstframe:
        #         if not keyframe:
        #             return
        #         else:s
        #             self._firstframe = False

        #     self.last_was_keyframe = keyframe
        #     if keyframe:
        #         self.last_keyframe = frame
        #         self.last_keyframe_timestamp = timestamp
        #     else:
        #         self.last_frame = frame
        #         self.last_timestamp = timestamp
        #     # print(f'Receiving frame {timestamp}: {len(frame)} B{" KEYFRAME" if keyframe else ""}')


class CameraVideoStreamTrack(MediaStreamTrack):

    kind = "video"

    async def recv(self) -> av.Packet:
        pass #sending directly



# class CameraVideoStreamTrack(MediaStreamTrack):

#     kind = "video"

#     def __init__(self, id_cam:str, id_peer:str, logger:RcutilsLogger, log_message_every_sec:float):
#         super().__init__()

#         self.id_peer:str = id_peer
#         self.id_cam:str = id_cam #or topic
#         self.logger:RcutilsLogger = logger

#         self.output:PacketsOutput = None
#         self.last_output_keyframe_timestamp:int = -1
#         self.last_output_timestamp:int = -1

#         self.total_processed:int = 0
#         self.log_message_every_sec:float = log_message_every_sec

#         self.last_log:float = -1.0

#     def set_sender(self, sender):
#         self._sender = sender

#     def set_output(self, output:PacketsOutput):
#         self.output = output

#     async def recv(self) -> av.Packet:

#         if not self.output:
#             return None

#         if self.output.last_keyframe is None:
#             return None

#         if self.last_output_keyframe_timestamp != self.output.last_keyframe_timestamp:
#             enncoded_frame = self.output.last_keyframe
#             timestamp = self.output.last_keyframe_timestamp
#             self.last_output_keyframe_timestamp = self.output.last_keyframe_timestamp
#         elif self.output.last_frame is not None and self.last_output_timestamp != self.output.last_timestamp:
#             enncoded_frame = self.output.last_frame
#             timestamp = self.output.last_timestamp
#             self.last_output_timestamp = self.output.last_timestamp
#         else:
#             return None

#         self.total_processed += 1

#         packet = av.Packet(enncoded_frame)
#         packet.pts = timestamp
#         packet.time_base = VIDEO_TIME_BASE

#         return packet # Tuple[List[bytes], int]