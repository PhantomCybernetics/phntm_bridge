
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput
from picamera2 import Picamera2
from aiortc.contrib.media import MediaStreamTrack
from typing import Tuple, Union, List
from termcolor import colored as c
import av
import fractions

from rclpy.impl.rcutils_logger import RcutilsLogger

from picamera2.outputs import FileOutput
from picamera2 import Picamera2
import libcamera

from aiortc import RTCRtpSender
import asyncio

VIDEO_CLOCK_RATE = 1000000000 #ns to s
# VIDEO_PTIME = 1 / 30  # 30fps
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)

class Picamera2Subscription:

    def __init__(self, id_camera:str, picam2:Picamera2, logger:RcutilsLogger):
        self.id_camera:str = id_camera
        self.num_received:int = 0
        self.peers:dict[str:RTCRtpSender] = {}
        self.logger:RcutilsLogger = logger

        self.picam2:Picamera2 = picam2;
        self.encoder:H264Encoder = None
        self.output:PacketsOutput = None

    async def start(self, id_peer:str, sender:RTCRtpSender) -> bool:

        if self.output != None:
            self.peers[id_peer] = sender
            sender.track.set_output(self.output)
            return True #all done, one sub for all

        # preview_config = self.picam2.create_preview_configuration(display='main',
        #                                                         encode='main',
        #                                                         transform=libcamera.Transform(hflip=1, vflip=1),
        #                                                         queue=False
        #                                                         )

        video_config = self.picam2.create_video_configuration(queue=False,
                                                              transform=libcamera.Transform(hflip=1, vflip=1))
        self.picam2.configure(video_config)
        self.encoder = H264Encoder(bitrate=5000000, framerate=30)
        self.output = PacketsOutput()

        self.peers[id_peer] = sender
        self.peers[id_peer].track.set_output(self.output)

        await asyncio.sleep(2.0) #camera setup time (here?)

        self.logger.info(c(f'Picam2 recording...', 'magenta'))

        # picam2.start_recording()
        self.picam2.start_encoder(encoder=self.encoder, output=self.output)
        self.picam2.start()

        return True

    def stop(self, id_peer:str):
        if id_peer in self.peers.keys():
            if self.peers[id_peer].track:
                self.peers[id_peer].track.set_output(None)
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
            f'picam2{c["Id"]}', #our id
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

    last_frame = None
    last_timestamp = 0
    last_keyframe = False

    def __init__(self):
        super().__init__()
        self.last_frame = None
        self.last_timestamp = 0
        self.last_keyframe = False

    def outputframe(self, frame, keyframe=True, timestamp=None):
        """Outputs frame from encoder

        :param frame: Frame
        :type frame: bytes
        :param keyframe: Whether frame is a keyframe, defaults to True
        :type keyframe: bool, optional
        :param timestamp: Timestamp of frame
        :type timestamp: int
        """
        if self.recording:
            if self._firstframe:
                if not keyframe:
                    return
                else:
                    self._firstframe = False
            self.last_frame = frame
            self.last_timestamp = timestamp
            self.last_keyframe = keyframe
            # print(f'Receiving frame {timestamp}: {len(frame)} B{" KEYFRAME" if keyframe else ""}')


class CameraVideoStreamTrack(MediaStreamTrack):

    kind = "video"

    def __init__(self, id_cam:str, id_peer:str, logger:RcutilsLogger, log_message_every_sec:float):
        super().__init__()

        self.id_peer:str = id_peer
        self.id_cam:str = id_cam
        self.logger:RcutilsLogger = logger

        self.output:PacketsOutput = None
        self.last_output_timestamp:int = -1

        self.total_processed:int = 0
        self.log_message_every_sec:float = log_message_every_sec

        self.last_log:float = -1.0

    def set_sender(self, sender):
        self._sender = sender

    def set_output(self, output:PacketsOutput):
        self.output = output

    async def recv(self) -> av.Packet:

        if not self.output:
            return None

        enncoded_frame = self.output.last_frame
        if enncoded_frame is None:
            return None

        if self.last_output_timestamp == self.output.last_timestamp: #no new frame
            return None
        self.last_output_timestamp = self.output.last_timestamp

        self.total_processed += 1

        packet = av.Packet(enncoded_frame)
        packet.pts = self.output.last_timestamp
        packet.time_base = VIDEO_TIME_BASE

        return packet # Tuple[List[bytes], int]