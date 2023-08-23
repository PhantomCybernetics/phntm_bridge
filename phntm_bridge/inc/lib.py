from rclpy.node import Node, Parameter, Subscription, QoSProfile, Publisher
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel, RTCConfiguration, RTCIceServer
from rclpy.impl.rcutils_logger import RcutilsLogger
from ..bridge import BridgeController

class TopicReadSubscription:
    sub:Subscription
    num_received:int
    last_msg:any
    last_msg_time:float
    peers: list[ str ]
    last_log:float
    # # raw_pipe_in: any
    # frame_processor:mp.Process
    # # raw_frames:mp.Queue
    # make_keyframe:mp.Value
    # make_h264:mp.Value
    # make_v8:mp.Value
    # processed_frames_h264:mp.Queue
    # processed_frames_v8:mp.Queue

    # def __init__(self, sub:Subscription, peers:list[str], frame_processor, processed_frames_h264:mp.Queue, processed_frames_v8:mp.Queue, make_keyframe_shared:mp.Value, make_h264_shared:mp.Value, make_v8_shared:mp.Value):
    def __init__(self, sub:Subscription, peers:list[str]):
        self.sub = sub
        self.num_received = 0
        self.last_msg = -1.0
        self.last_msg_time = -1.0
        self.peers = peers
        self.last_log = -1.0

        # self.frame_processor = frame_processor

        # # self.raw_frames = raw_frames
        # self.make_keyframe = make_keyframe_shared
        # self.make_h264 = make_h264_shared
        # self.make_v8 = make_v8_shared

        # self.processed_frames_h264 = processed_frames_h264
        # self.processed_frames_v8 = processed_frames_v8


class TopicWritePublisher:

    pub:Publisher
    num_written:int # num written msgs
    last_msg:any # last msg
    last_received_time:float # last msg received time(s)
    peers:list[str] #publishing peers
    last_time_logged:float #last time logged (s)

    def __init__(self, pub:Publisher, peers:list[str]):
        self.pub = pub
        self.num_written = 0
        self.last_msg = -1.0
        self.last_received_time = -1.0
        self.peers = peers
        self.last_time_logged = -1.0


class WRTCPeer:
    id: str
    pc: RTCPeerConnection
    node:BridgeController
    logger:RcutilsLogger
    # self.wrtc_peer_read_channels:dict[str,dict[str,RTCDataChannel]] = dict() # id_peer => [ topic => peer read webrtc channel ]
    # self.wrtc_peer_write_channels:dict[str,dict[str,RTCDataChannel]] = dict() # id_peer => [ topic => peer writen webrtc channel ]

    # self.wrtc_peer_video_tracks:dict[str,dict[str,RTCRtpSender]] = dict() # id_peer => [ topic => peer read webrtc video track ]
    # self.video_track_tmp:ROSVideoStreamTrack = None

    def __init__(self, id_peer:str, node:BridgeController):
        self.id = id_peer
        self.node = node
        self.logger = node.get_logger()
        config = RTCConfiguration(
            iceServers=[
                RTCIceServer( #TODO move this to server generated config
                    urls=[
                        "stun:stun.l.google.com:19302",
                        "stun:stun1.l.google.com:19302",
                        "stun:stun2.l.google.com:19302",
                        "stun:stun3.l.google.com:19302",
                        "stun:stun4.l.google.com:19302",
                        "turn:turn.phntm.io:3478",
                        "turn:turn.phntm.io:5349",
                    ],
                    credential="robopass",
                ),
            ]
        )
        self.pc = RTCPeerConnection(config)

        self.logger.info(f'Initial IceConnectionState: {self.pc.iceConnectionState} IceGatheringState: {self.pc.iceGatheringState}')

        if self.pc.connectionState in ['closed', 'failed']:
            self.logger.error(f'Peer WebRTC Connection is closed for {id_peer}')
            return

        @self.pc.on("connectionstatechange")
        async def on_connectionstatechange():
            self.logger.warn(f"WebRTC Connection (peer={id_peer}) state is %s" % self.pc.connectionState)
            if self.pc.connectionState == "failed":
               self.node.remove_peer()

        @self.pc.on("icegatheringstatechange")
        async def on_icegatheringstatechange():
            self.logger.info(f'WebRTC icegatheringstatechange (peer={id_peer}) state is %s' % self.pc.iceGatheringState)

        @self.pc.on("signalingstatechange")
        async def on_signalingstatechange():
            self.logger.info('signalingstatechange %s' % self.pc.signalingState)

        @self.pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            self.logger.info(f'WebRTC iceconnectionstatechange (peer={id_peer}) state is %s' % self.pc.iceConnectionState)


def get_peer_id(data:dict) -> str:
    id_peer:str = None
    if 'id_app' in data:
        id_peer = data['id_app']
    if 'id_instance' in data:
        id_peer = data['id_instance']
    return id_peer

# def key_in_tuple_list(key:str, search_list:list[tuple]):
#     for item in search_list:
#         if item[0] == key:
#             return True
#     return False


# def matches_param_filters(key:str, param:Parameter):
#     for test in param.get_parameter_value().string_array_value:
#         if test == '': continue

#         #if re.search(test, key):
#         #    return True
#         if test == '.*' or test == key[0:len(test)]:
#             return True
#     return False