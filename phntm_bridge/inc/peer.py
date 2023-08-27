import asyncio

from aiortc import RTCPeerConnection, RTCRtpSender, RTCSessionDescription, RTCDataChannel, RTCConfiguration, RTCIceServer

from rclpy.node import Node, Parameter, Subscription, QoSProfile, Publisher
from rclpy.duration import Duration, Infinite
from rclpy.impl.rcutils_logger import RcutilsLogger
from rosidl_runtime_py.utilities import get_message, get_interface
from rclpy.callback_groups import CallbackGroup
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy

from typing import Callable
import time

from .camera import CameraVideoStreamTrack

import threading

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
        self.outbound_data_channels:dict[str:RTCDataChannel] = {}

        self.video_tracks:dict[str:RTCRtpSender] = {}

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


    def GetId(data:dict) -> str:
        id_peer:str = None
        if 'id_app' in data:
            id_peer = data['id_app']
        if 'id_instance' in data:
            id_peer = data['id_instance']
        return id_peer