from rclpy.node import Node, Parameter, Subscription, QoSProfile, Publisher
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel, RTCConfiguration, RTCIceServer
from rclpy.impl.rcutils_logger import RcutilsLogger
from rosidl_runtime_py.utilities import get_message, get_interface
from rclpy.callback_groups import CallbackGroup
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy

import time

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


# one publisher for all peers
# this can indeed lead for compeition
class TopicWritePublisher:

    def __init__(self, node:Node, cbg:CallbackGroup, log_message_every_sec:float):
        self.pub:Publisher = None
        self.node:Node = node
        self.callback_group:CallbackGroup=cbg

        self.num_written:int = 0
        self.last_msg:any = None
        self.last_received_time:float = -1.0
        self.peers:list[str] = []
        self.last_time_logged:float = -1.0
        self.log_message_every_sec:float = log_message_every_sec

        self.topic:str = None
        self.protocol:str = None

    def start(self, id_peer:str, topic:str, protocol:str) -> bool:

        if self.topic != None and self.topic != topic:
            return False

        if self.protocol != None and self.protocol != protocol:
            return False

        if self.pub != None:
            if not id_peer in self.peers:
                self.peers.append(id_peer)
            return True #all done

        self.topic = topic
        self.protocol = protocol

        message_class = None
        try:
            message_class = get_message(self.protocol)
        except:
            pass

        if message_class == None:
            self.node.get_logger().error(f'NOT creating publisher for topic {self.topic}, msg class {self.protocol} not loaded')
            return False

        # reliable from here
        qos = QoSProfile(history=QoSHistoryPolicy.KEEP_LAST, \
                         depth=1, \
                         reliability=QoSReliabilityPolicy.BEST_EFFORT \
                         )

        self.pub = self.node.create_publisher(message_class, self.topic, qos, callback_group=self.callback_group)
        if self.pub == None:
            self.get_logger().error(f'Failed creating publisher for topic {topic}, protocol={protocol}, peer={id_peer}')
            return False

        if not id_peer in self.peers:
            self.peers.append(id_peer)

        return True

    def publish(self, id_peer:str, msg:any):

        self.num_written += 1
        self.last_msg = msg
        self.last_received_time = time.time()

        if time.time()-self.last_time_logged > self.log_message_every_sec:
            self.last_time_logged = time.time() #logged now
            if type(msg) is bytes:
                self.node.get_logger().info(f'▼ {self.topic} got message: {len(msg)}B from id_peer={id_peer}, total rcvd: {self.num_written}')
            else:
                self.node.get_logger().info(f'▼ {self.topic} got message: {len(msg)}, from id_peer={id_peer}, total rcvd: {self.num_written}')

        self.pub.publish(msg);

    def stop(self, id_peer:str) -> bool:
        if id_peer in self.peers:
            self.peers.remove(id_peer)

        if self.peers.count == 0:
            self.node.get_logger().info(f'Destroying local publisher for {self.topic}')

            self.pub.destroy()
            self.pub = None
            self.topic = None

            return True #destroyed
        else:
            return False


class WRTCPeer:

    # self.wrtc_peer_read_channels:dict[str,dict[str,RTCDataChannel]] = dict() # id_peer => [ topic => peer read webrtc channel ]
    # self.wrtc_peer_write_channels:dict[str,dict[str,RTCDataChannel]] = dict() # id_peer => [ topic => peer writen webrtc channel ]

    # self.wrtc_peer_video_tracks:dict[str,dict[str,RTCRtpSender]] = dict() # id_peer => [ topic => peer read webrtc video track ]
    # self.video_track_tmp:ROSVideoStreamTrack = None
    def __init__(self, id_peer:str, node:Node):
        self.id:str = id_peer
        self.node:Node = node
        self.logger:RcutilsLogger = node.get_logger()
        self.ros_publishers:list[str] = []
        self.inbound_data_channels:dict[str:RTCDataChannel] = {}

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
        self.pc:RTCPeerConnection = RTCPeerConnection(config)

        self.logger.info(f'Initial IceConnectionState: {self.pc.iceConnectionState} IceGatheringState: {self.pc.iceGatheringState}')

        if self.pc.connectionState in ['closed', 'failed']:
            self.logger.error(f'Peer WebRTC connection is closed or failed for {id_peer}')
            return

    def make_inbout_dc(self):
        pass


    def GetId(data:dict) -> str:
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

