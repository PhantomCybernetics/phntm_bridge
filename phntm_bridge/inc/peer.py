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

from termcolor import colored as c

# from .camera import CameraVideoStreamTrack

import threading

class WRTCPeer:

    # self.wrtc_peer_read_channels:dict[str,dict[str,RTCDataChannel]] = dict() # id_peer => [ topic => peer read webrtc channel ]
    # self.wrtc_peer_write_channels:dict[str,dict[str,RTCDataChannel]] = dict() # id_peer => [ topic => peer writen webrtc channel ]

    # self.wrtc_peer_video_tracks:dict[str,dict[str,RTCRtpSender]] = dict() # id_peer => [ topic => peer read webrtc video track ]
    # self.video_track_tmp:ROSVideoStreamTrack = None
    def __init__(self, id_peer:str, id_app:str, id_instance:str, session:str, ctrl_node:Node, ice_server_urls:list, ice_credential:str):

        self.id:str = id_peer
        self.id_app = id_app
        self.id_instance = id_instance
        self.sio_connected = True
        self.session = session
        
        self.topics_not_discovered:list[str] = []
        self.cameras_not_discovered:list[str] = []

        config = RTCConfiguration(
            iceServers=[
                RTCIceServer(
                    urls=ice_server_urls,
                    credential=ice_credential,
                ),
            ]
        )
        self.pc:RTCPeerConnection = RTCPeerConnection(config)

        self.node:Node = ctrl_node
        self.logger:RcutilsLogger = ctrl_node.get_logger()

        self.ros_publishers:list[str] = []

        self.inbound_data_channels:dict[str:RTCDataChannel] = {}
        self.outbound_data_channels:dict[str:RTCDataChannel] = {}
        self.video_tracks:dict[str:RTCRtpSender] = {}

        self.logger.info(c(f'Creating peer with iceServers={str(ice_server_urls)}', 'cyan'))

        self.read_subs:list(str) = []
        self.write_subs:list(list(str)) = []

        self.logger.info(f'Initial IceConnectionState: {self.pc.iceConnectionState},  IceGatheringState: {self.pc.iceGatheringState}')

        @self.pc.on("connectionstatechange")
        async def on_connectionstatechange():
            self.logger.warn(f"WebRTC {self} Connection State: {self.pc.connectionState}")

        @self.pc.on("icegatheringstatechange")
        async def on_icegatheringstatechange():
            self.logger.warn(f'"WebRTC {self} Ice Gathering State: {self.pc.iceGatheringState}')

        @self.pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            self.logger.warn(f'WebRTC {self} Ice Connection State: {self.pc.iceConnectionState}')

        @self.pc.on("signalingstatechange")
        async def on_signalingstatechange():
            self.logger.warn(f'WebRTC {self} Signaling State: {self.pc.signalingState}')

    def __str__(self):
        return f'Peer {self.id}'

    def GetId(data:dict) -> str:
        id_peer:str = None
        if 'id_app' in data:
            id_peer = data['id_app']
        if 'id_instance' in data:
            id_peer = data['id_instance']
        return id_peer