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
    def __init__(self, id_peer:str, id_app:str, id_instance:str, session:str, ctrl_node:Node, ice_servers:list, ice_username:str, ice_credential:str):

        self.id:str = id_peer
        self.id_app = id_app
        self.id_instance = id_instance
        self.sio_connected = True
        self.session = session
        
        self.wrtc_nextChannelId = 1
        
        self.topics_not_discovered:list[str] = []
        self.cameras_not_discovered:list[str] = []
        
        self.last_heartbeat:float = -1
        
        config = RTCConfiguration(
            iceServers=[
                RTCIceServer(
                    urls=ice_servers,
                    username=ice_username,
                    credential=ice_credential
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

        self.logger.info(c(f'Creating peer with iceServers={str(ice_servers)}', 'cyan'))

        self.read_subs:list(str) = []
        self.write_subs:list(list(str)) = []

        self.processing_subscriptions:bool = False
        
        self.logger.info(f'Initial IceConnectionState: {self.pc.iceConnectionState},  IceGatheringState: {self.pc.iceGatheringState}')

        @self.pc.on("connectionstatechange")
        async def on_connectionstatechange():
            self.logger.warn(f"WebRTC {self} Connection State: {self.pc.connectionState}")

        @self.pc.on("icegatheringstatechange")
        async def on_icegatheringstatechange():
            self.logger.warn(f'WebRTC {self} Ice Gathering State: {self.pc.iceGatheringState}')

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
    
    async def on_answer_reply(self, reply_data):
        if 'err' in reply_data.keys():
            msg = reply_data['msg'] if 'msg' in reply_data.keys() else None
            self.logger.error(f'Client returned error: {msg}')
            self.processing_subscriptions = False
            return

        if self.pc.signalingState != 'have-local-offer':
            self.logger.error(f'Not setting SDP answer from {self}, signalingState={self.pc.signalingState}')
            self.processing_subscriptions = False
            return
        
        self.logger.info(c(f'Got peer answer:', 'cyan'))
        print(reply_data)
        answer = RTCSessionDescription(sdp=reply_data['sdp'], type='answer')
        await self.pc.setRemoteDescription(answer)
        self.processing_subscriptions = False