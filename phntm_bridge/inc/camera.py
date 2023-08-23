
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput
from picamera2 import Picamera2
from aiortc.contrib.media import MediaStreamTrack
from typing import Tuple, Union, List
from termcolor import colored as c
import av
import fractions

VIDEO_CLOCK_RATE = 1000000000 #ns to s
# VIDEO_PTIME = 1 / 30  # 30fps
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)

class CameraSubscription:
    camera:any
    encoder: any
    output:any
    num_received:int
    peers: list[ str ]
    last_log:float

    def __init__(self, camera:any, encoder:any, output:any, peers:list[str]):
        self.camera = camera
        self.encoder = encoder
        self.output = output
        self.num_received = 0
        self.peers = peers
        self.last_log = -1.0


def get_camera_info(picam2:Picamera2) -> List[Tuple[str, dict]]:
    data = []
    info = picam2.global_camera_info()

    for c in info:
        print (str(c))
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
    # f:VideoFrame = None
    # encodedFrame:RTCEncodedFrame = None
    # frame_msg_bytes = None
    _timestamp = 0

    # _start: float
    # _timestamp: int
    _logger = None
    # _camera_subscriptions = None
    _id_cam = None
    _cam = None
    _id_peer = None
    _last_log_time = -1
    _log_message_every_sec = -1

    _total_processed = 0
    _sender = None

    _last_timestamp = -1
    _output = None

    # send_queue:mp.Queue = mp.Queue()

    def __init__(self, logger, id_cam, camera, output:PacketsOutput, id_peer, log_message_every_sec) -> None:
        super().__init__()
        self._logger = logger
        self._id_cam = id_cam
        self._cam = camera
        self._output = output
        # self._topic_read_subscriptions = topic_read_subscriptions
        self._id_peer = id_peer
        self._log_message_every_sec = log_message_every_sec
        self._last_timestamp = -1
        # self._logger.error(f'All good  in {topic}, enc={str(self.encoder)}')

    def set_sender(self, sender):
        self._sender = sender

    def get_logger(self):
        return self._logger

    async def recv(self) -> Tuple[List[bytes], int]: #returning what the worker therad alerady encoded and packetized

        frame = self._output.last_frame

        timestamp = self._output.last_timestamp
        keyframe = self._output.last_keyframe

        if frame is None:
            return None

        # print(c(f'I can has frame {len(frame) if frame is not None else "0"} B', 'cyan'))

        if self._last_timestamp == timestamp: #no new frame
            return None

        self._last_timestamp = timestamp

        self._total_processed += 1

        packet = av.Packet(frame)
        packet.pts = timestamp
        packet.time_base = VIDEO_TIME_BASE

        # print(f'Serving frame {timestamp}: {len(frame)} B{" KEYFRAME" if keyframe else ""}')

        # if is_keyframe or self._last_log_time < 0 or time.time()-self._last_log_time > self._log_message_every_sec:
        #     self._last_log_time = time.time() #last logged now
        #     self.get_logger().debug(f'△ {self._topic} peer={self._id_peer}, f:{self._total_processed}/{self._total_received}')

        # self.get_logger().error(f'{self._topic} recv returning packet data')

        return packet # Tuple[List[bytes], int]