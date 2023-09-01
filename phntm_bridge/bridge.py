import rclpy
from rclpy.node import Node, Parameter, QoSProfile, Publisher
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy
from rclpy.duration import Duration, Infinite
from rclpy.serialization import deserialize_message
from rclpy.callback_groups import CallbackGroup, MutuallyExclusiveCallbackGroup
from rclpy.context import Context
from rclpy.timer import Timer

from .inc.status_led import StatusLED
from termcolor import colored as c

import docker
docker_client = None
try:
    host_docker_socket = 'unix:///host_run/docker.sock' # link /var/run/ to /host_run/ in docker-compose
    # host_docker_socket = 'tcp://0.0.0.0:2375'
    docker_client = docker.DockerClient(base_url=host_docker_socket)
except Exception as e:
    print(c(f'Failed to init docker client with {host_docker_socket} {e}', 'red'))
    pass

from rcl_interfaces.msg import ParameterDescriptor
import signal
import time
import sys
import traceback
import netifaces

try:
    from picamera2 import Picamera2
except Exception as e:
    print(c('Failed to import picamera2', 'red'), e)
    pass

# if picam_detected:
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput
from picamera2 import Picamera2
import time
import libcamera
from .inc.camera import get_camera_info, picam2_has_camera, CameraVideoStreamTrack, PacketsOutput, Picamera2Subscription

picam2 = None
try:
    picam2 = Picamera2()
    print (c(f'Picamera2 global info: ', 'cyan') + str(picam2.global_camera_info()))
except Exception as e:
    print('Picamera2 not active')

from .inc.ros_video_streaming import ROSVideoStreamTrack, ROSFrameProcessor

# from rclpy.subscription import TypeVar
from rosidl_runtime_py.utilities import get_message, get_interface

import asyncio
import multiprocessing as mp
from queue import Empty, Full

import threading
import socketio

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer, MediaRelay
from aiortc.rtcrtpsender import RTCRtpSender, RTCEncodedFrame
from aiortc.contrib.media import MediaPlayer, MediaRelay
from aiortc.rtcrtpsender import RTCRtpSender

import os
import platform

from .inc.topic_reader import TopicReadSubscription
from .inc.topic_read_processor import TopicReadProcessor
from .inc.topic_writer import TopicWritePublisher
from .inc.peer import WRTCPeer

from .inc.introspection import Introspection
from .inc.config import BridgeControllerConfig

ROOT = os.path.dirname(__file__)

class BridgeController(Node, BridgeControllerConfig):

    ##
    # node constructor
    ##
    def __init__(self, context:Context, cbg:CallbackGroup, reader_ctrl_queue:mp.Queue, reader_out_queue:mp.Queue):
        super().__init__('phntm_bridge_ctrl', context=context)

        self.shutting_down:bool = False
        self.paused:bool = False
        self.callback_group:CallbackGroup = cbg

        self.get_logger().set_level(rclpy.logging.LoggingSeverity.DEBUG)
        self.load_config(self.get_logger())

        # separate process
        self.topic_read_subscriptions:dict[str: TopicReadSubscription] = {}

        # this process
        self.topic_write_publishers:dict[str: TopicWritePublisher] = {}
        self.camera_subscriptions:dict[str: Picamera2Subscription] = {}

        self.service_clients:dict[str: any] = {} # service name => client

        self.wrtc_nextChannelId = 1
        self.wrtc_peers:dict[str: WRTCPeer] = {}

        self.spin_thread: threading.Thread = None
        self.spin_task: asyncio.Future[any] = None
        self.sio_wait_task: asyncio.Future[any] = None
        self.sio_reconnect_wait_task: asyncio.Future[any] = None

        self.create_sio_client()

        discovery_period:float = self.get_parameter('discovery_period_sec').get_parameter_value().double_value
        stop_discovery_after:float = self.get_parameter('stop_discovery_after_sec').get_parameter_value().double_value
        self.introspection:Introspection = Introspection(period=discovery_period,
                                                         stop_after=stop_discovery_after,
                                                         ctrl_node=self,
                                                         picam2=picam2,
                                                         docker_client=docker_client,
                                                         sio=self.sio)

        self.conn_led = None
        if (self.conn_led_topic != None and self.conn_led_topic != ''):
            self.get_logger().info(f'CONN Led uses {self.conn_led_topic}')
            self.conn_led = StatusLED('conn', node=self, cbg=self.callback_group, mode=StatusLED.Mode.OFF, topic=self.conn_led_topic, qos=QoSProfile(depth=1, reliability=QoSReliabilityPolicy.BEST_EFFORT))
            self.conn_led.set_fast_pulse()
            #self.led_spinner = self.create_timer(0.1, lambda: rclpy.spin_once(self.status_led))
        self.data_led = None
        if (self.data_led_topic != None and self.data_led_topic != ''):
            self.get_logger().info(f'DATA Led uses {self.data_led_topic}')
            self.data_led = StatusLED('data', node=self, cbg=self.callback_group, mode=StatusLED.Mode.OFF, topic=self.data_led_topic, qos=QoSProfile(depth=1, reliability=QoSReliabilityPolicy.BEST_EFFORT))
            #self.data_led.off()

        self.reader_ctrl_queue:mp.Queue = reader_ctrl_queue
        self.reader_out_queue:mp.Queue = reader_out_queue

        self.get_logger().debug(f'Phntm Bridge started, idRobot={c(self.id_robot, "cyan")}')

    ##
    # make a socket.io instance
    ##
    def create_sio_client(self):

        self.sio = socketio.AsyncClient(handle_sigint=False,
                                        logger=False,
                                        ssl_verify=self.sio_ssl_verify
                                        )

        @self.sio.on('connect')
        async def on_connect():
            self.get_logger().info('Socket.io connection established, auth successful')

            self.sio.connected = True #socket.io sets this after callback, makes report calls work immediately

            if self.conn_led != None:
                self.conn_led.on()

            # TODO: maybe report to clients without server caching?
            asyncio.get_event_loop().create_task(self.introspection.report_cameras())
            asyncio.get_event_loop().create_task(self.introspection.report_topics())
            asyncio.get_event_loop().create_task(self.introspection.report_services())
            asyncio.get_event_loop().create_task(self.introspection.report_docker())
            asyncio.get_event_loop().create_task(self.introspection.report_introspection())

        event_loop = asyncio.get_event_loop()

        @self.sio.on('offer')
        async def on_offer(data):
            id_peer:str = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            return await self.on_peer_wrtc_offer(id_peer, data)

        @self.sio.on('discovery')
        async def on_discovery(data):
            id_peer:str = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            if not id_peer in self.wrtc_peers.keys():
                return { 'err': 2, 'msg': 'Peer not connected' }
            new_state = True if data['state'] else False
            if new_state:
                asyncio.get_event_loop().create_task(self.introspection.start())
            else:
                asyncio.get_event_loop().create_task(self.introspection.stop())
                # await self.introspection.stop()
            return { 'success': 1, 'discovery': self.introspection.running }

        @self.sio.on('docker')
        async def on_docker_call(data):
            id_peer:str = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            if not id_peer in self.wrtc_peers.keys():
                return { 'err': 2, 'msg': 'Peer not connected' }
            id_container = data['container']
            msg = data['msg']
            if not id_container:
                return { 'err': 2, 'msg': 'No container id provided' }
            if not msg:
                return { 'err': 2, 'msg': 'No container msg provided' }

            cont = self.introspection.discovered_docker_containers[id_container]
            if not cont:
                return { 'err': 2, 'msg': 'Container not found here'}

            self.get_logger().warn(f'Peer {id_peer} calling {msg} on docker container {cont[0].name} [{cont[0].status}]')

            match msg:
                case 'start':
                    event_loop.run_in_executor(None, cont[0].start)
                case 'stop':
                    event_loop.run_in_executor(None, lambda: cont[0].stop(timeout=3)) # wait 3s then kill
                case 'restart':
                    event_loop.run_in_executor(None, lambda: cont[0].restart(timeout=3) ) # wait 3s then kill
                case _:
                   return { 'err': 2, 'msg': 'Invalid container action provided (start, stop or restart)'}

            asyncio.get_event_loop().create_task(self.introspection.start())

            return { 'success': 1 }

        # subscribe and unsubscribe data channels
        # bcs the negotionation doesn't seem to be implemented well at the moment
        # it's be nice to do thisvia webrtc tho
        @self.sio.on('subscription:read')
        async def on_read_subscription(data:dict):
            id_peer = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            if not id_peer in self.wrtc_peers.keys():
                return { 'err': 2, 'msg': 'Peer not connected' }
            return await event_loop.create_task(self.on_read_subscriptions_change(id_peer, data))

        # WRITE SUBS
        @self.sio.on('subscription:write')
        async def on_write_subscription(data:dict):
            id_peer = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            if not id_peer in self.wrtc_peers.keys():
                 return { 'err': 2, 'msg': 'Peer not connected' }
            return await self.on_write_subscription_change(id_peer, data)

        # SERVICE CALLS
        @self.sio.on('service')
        async def on_peer_service_call(data:dict):
            id_peer = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            if not id_peer in self.wrtc_peers.keys():
                return { 'err': 2, 'msg': 'Peer not connected' }
            return await self.on_service_call(id_peer, data)

        # subscribe and unsubscribe camera streams
        @self.sio.on('cameras:read')
        async def on_cameras_subscription(data:dict):
            id_peer = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            if not id_peer in self.wrtc_peers.keys():
                 return { 'err': 2, 'msg': 'Peer not connected' }
            if not 'cameras' in data:
                return { 'err': 2, 'msg': 'No cameras specified' }
            return await self.on_camera_subscription_change(id_peer, data)

        @self.sio.event
        async def connect_error(data):
            self.get_logger().error('Socket.io connection failed: ' + str(data))

        @self.sio.on('peer:disconnected')
        async def on_peer_disconnected(data:dict):
            id_peer = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            if not id_peer in self.wrtc_peers.keys():
                return { 'err': 2, 'msg': 'Peer not connected' }
            self.get_logger().warn(f'Peer {id_peer} disconnected from Socket.io server (fyi, ignoring)')

        @self.sio.on('message')
        async def on_message(data):
            self.get_logger().warn('Unhandled: Socket.io message received with ' + str(data))
            self.sio.send({'response': '?'})

        @self.sio.on('*')
        async def catch_all(event, data):
            self.get_logger().warn('Unhandled: Socket.io event ' + str(event) + ' with ' + str(data))

        @self.sio.on('disconnect')
        async def on_disconnect():
            self.get_logger().warn('Socket.io disconnected from server')
            if self.conn_led != None:
                if not self.shutting_down:
                    self.conn_led.set_fast_pulse()
                else:
                    self.conn_led.off()

    ##
    # spin socket.io
    ##
    async def spin_sio_client(self, addr:str, port:int, path:str):
        while not self.shutting_down:
            try:
                self.get_logger().info(f'Socket.io connecting to {addr}{path}:{port}')
                auth_data = {
                    'id_robot': self.id_robot,
                    'key': self.auth_key,
                    'name': self.robot_name
                }
                await self.sio.connect(url=f'{addr}:{port}', socketio_path=path, auth=auth_data)

                self.sio_wait_task = asyncio.get_event_loop().create_task(self.sio.wait()) # wait as long as connected
                await self.sio_wait_task
                self.sio_wait_task = None
            except socketio.exceptions.ConnectionError:
                self.get_logger().warn(f'Socket.io connection error, retrying in {self.sio_connection_retry_sec}s...')
                self.sio_reconnect_wait_task = asyncio.get_event_loop().create_task(asyncio.sleep(self.sio_connection_retry_sec))
                await self.sio_reconnect_wait_task
                self.sio_reconnect_wait_task = None
            except asyncio.CancelledError:
                self.get_logger().info('CancelledError')
                return

    ##
    # read and disctribute queued ros data
    ##
    async def read_queued_data(self):
        # newest:dict[str:any] = {}
        while not self.shutting_down:
            # while True:
            try:
                # print('read_queued_data >')
                [topic, msg] = await asyncio.get_event_loop().run_in_executor(None, self.reader_out_queue.get) #blocks
                # print('  < read_queued_data')

                if topic in self.topic_read_subscriptions.keys():
                    asyncio.get_event_loop().run_in_executor(None, self.topic_read_subscriptions[topic].on_msg, msg)
                    # await asyncio.sleep(.01)
                # newest[topic] = msg
                # self.get_logger().warn(f'[{topic}] has {len(msg)} B')
            except Empty:
                self.get_logger().warn(f'queue empty')
                pass

            # for topic in newest.keys():
            #     if topic in self.topic_read_subscriptions.keys():
            #         self.topic_read_subscriptions[topic].on_msg(msg)
            #         # await asyncio.sleep(.01)

            # newest.clear()
            # await asyncio.sleep(.01)

    ##
    # init p2p connection with a peer sdp offer
    ##
    async def on_peer_wrtc_offer(self, id_peer:str, offerData:dict):

        if not 'sdp' in offerData.keys():
            return { 'err': 2, 'msg': 'Offer missing sdp' }
        if not 'type' in offerData.keys():
            return { 'err': 2, 'msg': 'Offer missing type' }

        offer = RTCSessionDescription(sdp=offerData["sdp"], type=offerData["type"])

        self.get_logger().debug(c('Got SDP offer from '+id_peer, 'cyan'))
        if self.log_sdp:
            print(c(offer.sdp, 'dark_grey'))

        if id_peer in self.wrtc_peers.keys():
            self.get_logger().debug(f'Peer {id_peer} was already connected, removing...')
            await self.remove_peer(id_peer, wait=False)

        # pc.addTransceiver('video', direction='sendonly') #must have at least one
        peer = WRTCPeer(id_peer, self)
        self.wrtc_peers[id_peer] = peer
        @peer.pc.on("connectionstatechange")
        async def on_connectionstatechange():
            self.get_logger().warn(f"WebRTC (peer={id_peer}) Connection state: %s" % peer.pc.connectionState)
            if peer.pc.connectionState == "failed":
               await self.remove_peer(peer.id, wait=True)

        @peer.pc.on("icegatheringstatechange")
        async def on_icegatheringstatechange():
            self.get_logger().warn(f'WebRTC(peer={id_peer}) Ice Gathering State: %s' % peer.pc.iceGatheringState)

        @peer.pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            self.get_logger().warn(f'WebRTC (peer={id_peer}) Ice Connection State: %s' % peer.pc.iceConnectionState)

        @peer.pc.on("signalingstatechange")
        async def on_signalingstatechange():
            self.get_logger().warn(f'WebRTC (peer={id_peer}) Signaling State: %s' % peer.pc.signalingState)

        await peer.pc.setRemoteDescription(offer)

        answer = await peer.pc.createAnswer()
        await peer.pc.setLocalDescription(answer)

        async def ice_checker():
            while peer.pc.iceGatheringState != 'complete':
                await asyncio.sleep(.1)
            self.get_logger().info(f'Intial Ice Gathering unlocked w State: {peer.pc.iceGatheringState}')

        await ice_checker()

        self.get_logger().debug(c('Generated answer', 'cyan'))
        if self.log_sdp:
            print(c(peer.pc.localDescription.sdp, 'dark_grey'))

        return { 'sdp': peer.pc.localDescription.sdp, 'type':peer.pc.localDescription.type }


    # WRTC peer disconnected
    # optionally wait a bit for reconnect, then clean up
    async def remove_peer(self, id_peer:str, wait:bool=True):
        if not id_peer in self.wrtc_peers.keys():
            return

        if wait:
            self.get_logger().info(f'Peer {id_peer} seems to be disconnected (waiting 10s...)')

            self.paused = True
            await asyncio.sleep(10.0) #wait a bit for reconnects

            if not id_peer in self.wrtc_peers.keys():
                return

            if self.wrtc_peers[id_peer].pc.connectionState in []:
                self.get_logger().info(f'Peer {id_peer} recovered, we good')
                return

        self.get_logger().info(c(f'Peer {id_peer} disconnected, cleaning up', 'red'))

        # read topics
        delete_topics = []
        for topic in self.topic_read_subscriptions.keys():
            if self.topic_read_subscriptions[topic].stop(id_peer): # subscriber destroyed
                delete_topics.append(topic)
        for topic in delete_topics:
            del self.topic_read_subscriptions[topic]

        # wite tpics
        delete_topics = []
        for topic in self.topic_write_publishers.keys():
            if self.topic_write_publishers[topic].stop(id_peer): # publisher destroyed
                delete_topics.append(topic)
        for topic in delete_topics:
            del self.topic_write_publishers[topic]

        # cameras
        delete_cams = []
        for cam in self.camera_subscriptions.keys():
            if self.camera_subscriptions[cam].stop(id_peer): # camera destroyed
                delete_cams.append(cam)
        for cam in delete_cams:
            del self.camera_subscriptions[cam]

        if id_peer in self.wrtc_peers.keys():
            try:
                await self.wrtc_peers[id_peer].pc.close()
            except Exception as e:
                pass
            del self.wrtc_peers[id_peer]


    ##
    # Topic READ subscriptions & message routing
    ##
    async def on_read_subscriptions_change(self, id_peer:str, data:dict) -> {}:

        if not 'topics' in data:
            self.get_logger().error(f'No topics specified in on_read_subscriptions_change, peer={id_peer}')
            return { 'err': 2, 'msg': 'No topics specified' }

        # self.get_logger().info(f'Peer {id_peer} subscription change for {len(data["topics"])} topics')

        negotiation_needed = False
        for topic_data in data['topics']: # : [ topic, subscribe, ...]
            if int(topic_data[1]) > 0: # subscribe
                negotiation_needed = True
                break

        if negotiation_needed and not 'sdp_offer' in data:
            self.get_logger().error(f'Negotiation needed but no SDP provided in on_read_subscriptions_change, peer={id_peer}')
            return { 'err': 2, 'msg': 'Missing sdp_offer (negotiation needed on new subscriptions)' }

        peer:WRTCPeer = self.wrtc_peers[id_peer]
        if not peer:
            self.get_logger().error(f'Peer not connected in on_read_subscriptions_change, peer={id_peer}')
            return { 'err': 2, 'msg': 'Peer not connected' }

        async def signalling_stable_checker():
            timeout_sec = 10.0
            while peer.pc.signalingState != 'stable' and timeout_sec > 0.0:
                await asyncio.sleep(.1)
                timeout_sec -= .1
            if timeout_sec <= 0.0:
                self.get_logger().error(f'Timed out waiting for stable signalling state in on_read_subscriptions_change, peer={id_peer}')
                return { 'err': 2, 'msg': 'Timed out waiting for stable signalling state' }

        if negotiation_needed:
            await signalling_stable_checker()
            offer = RTCSessionDescription(sdp=data['sdp_offer'], type='offer')
            self.get_logger().debug(c(f'Setting new peer SDP offer from peer {id_peer}', 'cyan'))
            if self.log_sdp:
                print(c(data['sdp_offer'], 'dark_grey'))
            await peer.pc.setRemoteDescription(offer)

        res_subscribed:list[tuple[str,int]] = [] # [ topic, id_ch ]
        res_unsubscribed:list[tuple[str,int]] = []
        res_err:list[tuple[str,str]] = []

        for topic_data in data['topics']: # : [ topic, subscribe, ...]
            topic:str = topic_data[0]
            subscribe:bool = int(topic_data[1]) > 0

            if subscribe:
                #print(f'on_read_subscriptions_change:subscribe {threading.get_ident()}')

                # we wouldn't have to wait for discovery if we required msg type from the client (?)
                if not topic in self.introspection.discovered_topics.keys():
                    self.get_logger().debug(f'Topic {topic} not discovered yet in on_read_subscriptions_change, peer {id_peer}')
                    res_err.append([topic, 'Not discovered yet'])
                    continue

                protocol:str = ', '.join(self.introspection.discovered_topics[topic]['msg_types'])
                is_image = protocol == 'sensor_msgs/msg/Image'

                # subscribe binary data channels
                if not is_image:

                    if not topic in self.topic_read_subscriptions:

                        reliability = self.get_parameter_or(f'{topic}.reliability', Parameter(name='', value=QoSReliabilityPolicy.BEST_EFFORT)).get_parameter_value().integer_value
                        durability = self.get_parameter_or(f'{topic}.durability', Parameter(name='', value=DurabilityPolicy.VOLATILE)).get_parameter_value().integer_value
                        self.topic_read_subscriptions[topic] = TopicReadSubscription(ctrl_node=self,
                                                                                     reader_ctrl_queue=self.reader_ctrl_queue,
                                                                                     topic=topic,
                                                                                     protocol=protocol,
                                                                                     reliability=reliability,
                                                                                     durability=durability,
                                                                                     cbg=self.callback_group,
                                                                                     event_loop=asyncio.get_event_loop(),
                                                                                     log_message_every_sec=self.log_message_every_sec
                                                                                    )
                        def on_msg_cb():
                            if self.data_led != None:
                                self.data_led.once() # blink when sending data to a peer
                        self.topic_read_subscriptions[topic].on_msg_cb = lambda: on_msg_cb() #blinker

                    if not topic in peer.outbound_data_channels.keys():
                        self.wrtc_nextChannelId += 1
                        dc:RTCDataChannel = peer.pc.createDataChannel(topic,
                                                                      id=self.wrtc_nextChannelId,
                                                                      protocol=protocol,
                                                                      negotiated=True,
                                                                      ordered=False,
                                                                      maxRetransmits=1,
                                                                    #   maxPacketLifeTime=10 #ms
                                                                      )
                        peer.outbound_data_channels[topic] = dc
                        self.get_logger().debug(f'Peer {id_peer} subscribed to {topic} (protocol={protocol}, ch_id={dc.id})')

                    if not self.topic_read_subscriptions[topic].start(id_peer, peer.outbound_data_channels[topic]):
                        self.get_logger().error(f'Topic {topic} failed to subscribee in on_read_subscriptions_change, peer={id_peer}')
                        return { 'err': 2, 'msg': f'Topic {topic} failed to subscribe'}

                    res_subscribed.append([topic, peer.outbound_data_channels[topic].id, protocol])
                    await self.topic_read_subscriptions[topic].report_latest_when_ready(id_peer)

                else: #subscribe image
                    #TODO

                    self.get_logger().error(f'Image topics not re-implemented yet ({topic}), peer={id_peer}')

                    pass
                    # if not self.subscribe_topic(topic, id_peer):
                    #     res_err.append([topic, 'Image topic failed to subscribe'])
                    #     continue

                    # if not id_peer in self.wrtc_peer_video_tracks.keys():
                    #     self.wrtc_peer_video_tracks[id_peer] = dict()

                    # sender:RTCRtpSender = None
                    # if topic in self.wrtc_peer_video_tracks[id_peer].keys(): # never destroyed during p2p session
                    #     sender = self.wrtc_peer_video_tracks[id_peer][topic]
                    # else:
                    #     track = ROSVideoStreamTrack(self.get_logger(), topic, self.topic_read_subscriptions, id_peer, self.log_message_every_sec)
                    #     sender = pc.addTrack(track)
                    #     track.set_sender(sender) # recv() needs to know the encoder class

                    #     # sender.__logger = self.get_logger()
                    #     # aiortc sets _stream_id to self.__stream_id in RTCPeerConnection.__createTransceiver
                    #     # this makes it impossible to identify streams on received
                    #     # this should fix it for now
                    #     sender._stream_id = sender._track_id
                    #     self.get_logger().warn(f'Created sender for id_peer={id_peer} {topic}, _stream_id={sender._stream_id} _track_id=={sender._track_id}, track={str(sender.track)}')

                    #     @sender.track.on('ended')
                    #     async def on_sender_track_ended():
                    #         self.get_logger().warn(f'Sender track ended, _stream_id={str(sender._stream_id)} _track_id=={str(sender._track_id)}')
                    #         # for line in traceback.format_stack():
                    #         #     print(line.strip())
                    #         # await pc.removeTrack(sender)
                    #         # if topic in self.wrtc_peer_video_tracks[id_peer]:
                    #         #     self.wrtc_peer_video_tracks[id_peer].pop(topic)

                    #     self.wrtc_peer_video_tracks[id_peer][topic] = sender
                    #     # await sender.track.set_frame(av.VideoFrame(width=640, height=480, format='rgb24'))
                    #     self.get_logger().info(f'Sender capabilities: {str(sender.getCapabilities(kind="video"))}')

                    # # sender.pause(False);
                    # res_subscribed.append([topic, sender._track_id ]) #id is generated str

            else: # unsubscribe

                if topic in peer.outbound_data_channels.keys():
                    self.get_logger().debug(f'Peer {id_peer} no longer subscribing to {topic}')
                    id_closed_dc = peer.outbound_data_channels[topic].id
                    peer.outbound_data_channels[topic].close()
                    peer.outbound_data_channels.pop(topic)
                    res_unsubscribed.append([ topic, id_closed_dc ])

                if topic in self.topic_read_subscriptions:
                    if self.topic_read_subscriptions[topic].stop(id_peer):
                        self.topic_read_subscriptions.pop(topic)

        reply_data = {
            'success': 1,
            'subscribed': res_subscribed,
            'unsubscribed': res_unsubscribed,
            'err': res_err
        }

        if negotiation_needed:
            self.get_logger().info(c(f'Creating SDP answer for peer={id_peer}', 'cyan'))
            answer = await peer.pc.createAnswer()
            if self.log_sdp:
                print(c(answer, 'dark_grey'))
            await peer.pc.setLocalDescription(answer)
            reply_data['answer_sdp'] = peer.pc.localDescription.sdp

        return reply_data


    ##
    # Topic WRITE subscriptions & message routing
    ###
    async def on_write_subscription_change(self, id_peer:str, data:dict) -> {}:
        peer:WRTCPeer = self.wrtc_peers[id_peer]

        if not 'topics' in data:
            self.get_logger().error(f'No topics specified in on_write_subscription_change, peer={id_peer}')
            return { 'err': 2, 'msg': 'No topics specified' }

        if not peer:
            self.get_logger().error(f'Peer not connected in on_write_subscription_change, peer={id_peer}')
            return { 'err': 2, 'msg': 'Peer not connected' }

        res_subscribed:list[tuple[str,int]] = list() # [ topic, id_ch ]
        res_unsubscribed:list[tuple[str,int]] = list()

        for topic_data in data['topics']: # : [ topic, subscribe, ...]
            topic:str = topic_data[0]
            subscribe:bool = int(topic_data[1]) > 0
            protocol:str = topic_data[2]

            if protocol == None:
                self.get_logger().error(f'Protocol not specified for {topic} in on_write_subscription_change, peer={id_peer}')
                return { 'err': 2, 'msg': f'Protocol not specified for {topic}' }

            if subscribe:

                if not topic in self.topic_write_publishers:
                    self.topic_write_publishers[topic] = TopicWritePublisher(node=self,
                                                                              topic=topic,
                                                                              protocol=protocol,
                                                                              cbg=self.callback_group,
                                                                              log_message_every_sec=self.log_message_every_sec)

                if not self.topic_write_publishers[topic].start(id_peer):
                    self.get_logger().error(f'Topic {topic} failed to start publisher in on_write_subscription_change, peer={id_peer}')
                    return { 'err': 2, 'msg': f'Topic {topic} failed to create publisher'}

                if not topic in peer.inbound_data_channels.keys():
                    self.wrtc_nextChannelId += 1
                    dc:RTCDataChannel = peer.pc.createDataChannel(topic,
                                                                  id=self.wrtc_nextChannelId,
                                                                  protocol=protocol,
                                                                  negotiated=True,
                                                                  ordered=False,
                                                                  maxRetransmits=0)
                    peer.inbound_data_channels[topic] = dc
                    self.get_logger().debug(f'Peer {id_peer} publishing into {topic} (protocol={protocol}, ch_id={dc.id})')

                    @dc.on('message')
                    async def on_inbound_channel_message(msg):
                        self.topic_write_publishers[topic].publish(id_peer, msg)

                res_subscribed.append([topic, peer.inbound_data_channels[topic].id, protocol])

            else: # unsubscribe

                if topic in peer.inbound_data_channels.keys():
                    self.get_logger().debug(f'Peer {id_peer} no longer publishing into {topic}')
                    id_closed_dc = peer.inbound_data_channels[topic].id
                    peer.inbound_data_channels[topic].close()
                    peer.inbound_data_channels.pop(topic)
                    res_unsubscribed.append([ topic, id_closed_dc ])

                if topic in self.topic_write_publishers:
                    if self.topic_write_publishers[topic].stop(id_peer):
                        self.topic_write_publishers.pop(topic)

        return { 'success': 1, 'subscribed': res_subscribed, 'unsubscribed': res_unsubscribed}


    ##
    # Camera feed subscriptions
    ##
    async def on_camera_subscription_change(self, id_peer:str, data:dict):
        # self.get_logger().info(f'Peer {id_peer} subscriptoin change for cameras: {str(data["cameras"])}')

        negotiation_needed = False
        for camera_data in data['cameras']: # : [ topic, subscribe, ...]
            if int(camera_data[1]) > 0: #subscribe
                negotiation_needed = True
                break

        if negotiation_needed and not 'sdp_offer' in data:
            self.get_logger().error(f'Negotiation needed but no SDP provided in on_camera_subscription, peer={id_peer}')
            return { 'err': 2, 'msg': 'Missing sdp_offer (negotiation needed on new camera subscriptions)' }

        peer:WRTCPeer = self.wrtc_peers[id_peer]
        if not peer:
            self.get_logger().error(f'Peer not connected in on_camera_subscription, peer={id_peer}')
            return { 'err': 2, 'msg': 'Peer not connected' }

        async def signalling_stable_checker() -> bool:
            timeout_sec = 10.0
            while peer.pc.signalingState != 'stable' and timeout_sec > 0.0:
                await asyncio.sleep(.1)
                timeout_sec -= .1
            if timeout_sec <= 0.0:
                return False
            return True

        if negotiation_needed:
            if not await signalling_stable_checker():
                self.get_logger().error(f'Timed out waiting for stable signalling state in on_camera_subscription, peer={id_peer}')
                return { 'err': 2, 'msg': 'Timed out waiting for stable signalling state' }

            offer = RTCSessionDescription(sdp=data['sdp_offer'], type='offer')
            self.get_logger().info(c(f'Setting new peer SDP offer for peer={id_peer}', 'cyan'))
            if self.log_sdp:
                print(c(offer, 'dark_grey'))
            await peer.pc.setRemoteDescription(offer)

        res_subscribed:list[tuple[str,int]] = list() # [ cam, id_ch ]
        res_unsubscribed:list[tuple[str,int]] = list()
        res_err:list[tuple[str,str]] = list()

        #  self.get_logger().warn(f'setRemoteDescription {data["offer"]}')

        for camera_data in data['cameras']: # : [ cam, subscribe, ...]
            id_cam:str = camera_data[0]
            subscribe:bool = int(camera_data[1]) > 0

            if subscribe:

                if id_cam.startswith('picam2'):
                    if picam2 is None:
                        res_err.append([id_cam, 'Picamera2 not available'])
                        continue
                    if not picam2_has_camera(picam2, id_cam):
                        res_err.append([id_cam, 'Not available via Picamera2'])
                        continue
                else:
                    res_err.append([id_cam, 'Unsupported camera type'])
                    continue

                if not id_cam in self.camera_subscriptions:
                     self.camera_subscriptions[id_cam] = Picamera2Subscription(id_camera=id_cam,
                                                                               picam2=picam2,
                                                                               logger=self.get_logger()
                                                                            ) #TODO config

                if not id_cam in peer.video_tracks:
                    track = CameraVideoStreamTrack(id_cam=id_cam,
                                                   id_peer=id_peer,
                                                   logger=self.get_logger(),
                                                   log_message_every_sec=self.log_message_every_sec)

                    sender:RTCRtpSender = peer.pc.addTrack(track)
                    track.set_sender(sender) # recv() needs to know the encoder class
                    sender._stream_id = sender._track_id

                    self.get_logger().info(f'Created video sender for peer={id_peer} {id_cam}, track_id=={sender._track_id}, capabilities: {str(sender.getCapabilities(kind="video"))}')

                    @sender.track.on('ended')
                    async def on_sender_track_ended():
                        self.get_logger().warn(f'Sender track ended for peer={id_peer}, track_id=={str(sender._track_id)}')

                    peer.video_tracks[id_cam] = sender
                    # await sender.track.set_frame(av.VideoFrame(width=640, height=480, format='rgb24'))

                if not await self.camera_subscriptions[id_cam].start(id_peer, peer.video_tracks[id_cam]):
                    self.get_logger().error(f'Camera {id_cam} failed to subscribe, peer={id_peer}')
                    return { 'err': 2, 'msg': f'Camera {id_cam} failed to subscribe'}

                res_subscribed.append([id_cam, peer.video_tracks[id_cam]._track_id ]) #id is generated str

            else: # unsubscribe

                if id_cam in peer.video_tracks.keys():
                    self.get_logger().debug(f'Peer {id_peer} no longer subscribed to {id_cam}; leavimg channel open')

                if id_cam in self.camera_subscriptions:
                    if self.camera_subscriptions[id_cam].stop(id_peer): # cam destroyed
                        self.camera_subscriptions.pop(id_cam)

            #     self.unsubscribe_topic(topic, id_peer)

            #     if id_peer in self.wrtc_peer_read_channels and topic in self.wrtc_peer_read_channels[id_peer]:
            #         self.get_logger().info(f'Peer {id_peer} unsubscribed from {topic} R')
            #         id_closed_dc = self.wrtc_peer_read_channels[id_peer][topic].id
            #         self.wrtc_peer_read_channels[id_peer][topic].close()
            #         self.wrtc_peer_read_channels[id_peer].pop(topic)
            #         res_unsubscribed.append([ topic, id_closed_dc ])

            #     if id_peer in self.wrtc_peer_video_tracks and topic in self.wrtc_peer_video_tracks[id_peer]:
            #         self.get_logger().info(f'Peer {id_peer} unsubscribed from {topic} /R IMG (NOT EVEN pausing stream)')
            #         # self.wrtc_peer_video_tracks[id_peer][topic].pause()
            #         # self.wrtc_peer_video_tracks[id_peer][topic].pause()
            #         # id_closed_track = self.wrtc_peer_video_tracks[id_peer][topic].track.id # str generated
            #         # if id_peer in self.wrtc_peer_video_tracks:
            #         #     if topic in self.wrtc_peer_video_tracks[id_peer]:
            #         #         if self.wrtc_peer_video_tracks[id_peer][topic].track:
            #         #             self.get_logger().info(f'Peer {id_peer} stopping topic {topic}...')
            #         #             await pc.removeTrack(self.wrtc_peer_video_tracks[id_peer][topic])
            #         #             # await self.wrtc_peer_video_tracks[id_peer][topic].stop()
            #         #             self.get_logger().info(f'Peer {id_peer} stopped topic {topic}...')
            #         #         if topic in self.wrtc_peer_video_tracks[id_peer]:
            #         #             self.wrtc_peer_video_tracks[id_peer].pop(topic)
            #         res_unsubscribed.append([ topic ])

        reply_data = {
            'success': 1,
            'subscribed': res_subscribed,
            'unsubscribed': res_unsubscribed,
            'err': res_err
        }

        if negotiation_needed:
            self.get_logger().info(c(f'Generating answer for peer={id_peer}', 'cyan'))
            answer = await peer.pc.createAnswer()
            if self.log_sdp:
                print(c(answer, 'dark_grey'))
            await peer.pc.setLocalDescription(answer)
            reply_data['answer_sdp'] = peer.pc.localDescription.sdp

        return reply_data


    ##
    # ROS Service call handling
    ##
    async def on_service_call(self, id_peer:str, data:dict):
        service = data['service']
        if not service:
            self.get_logger().error(f'No service name provided by peer={id_peer}, ignoring call')
            return { 'err': 2, 'msg': f'No service name provided' }

        self.get_logger().debug(f"Peer {id_peer} calling service {service} with args: {str(data['msg'])}")

        if not service in self.introspection.discovered_services.keys():
            self.get_logger().error(f'Service {service} not discovered (yet?) for peer={id_peer}')
            return { 'err': 2, 'msg': f'Service {service} not discovered (yet?)' }

        message_class = None
        msg_type = {self.introspection.discovered_services[service]["msg_types"][0]}
        try:
            message_class = get_interface(msg_type)
        except:
            pass
        if message_class == None:
            self.get_logger().error(f'NOT calling service {service}, msg class {msg_type} not loaded, peer={id_peer}')
            return { 'err': 2, 'msg': f'Message class {msg_type} not loaded here' }

        cli = None
        if service in self.service_clients.keys():
            cli = self.service_clients[service]
        else:
            cli = self.create_client(message_class, service)
            self.service_clients[service] = cli

        if cli == None:
            self.get_logger().error(f'Failed to create client for service {service}, msg class={msg_type}, peer={id_peer}')
            return { 'err': 2, 'msg': f'Failed to create service client' }

        async def srv_ready_checker():
            timeout_sec = 10.0
            while cli.context.ok() and not cli.service_is_ready() and timeout_sec > 0.0:
                await asyncio.sleep(.1)
                timeout_sec -= .1
        await srv_ready_checker()

        if not cli.service_is_ready():
            self.get_logger().error(f'Service client for {service} still not ready, giving up, peer={id_peer}')
            return { 'err': 2, 'msg': f'Service client init timeout' }

        payload = None
        if 'msg' in data.keys():
            payload = data['msg']
        req = None
        if payload:
            req = message_class.Request(data=payload)
        else:
            req = message_class.Request()
        future = cli.call_async(req)
        # rclpy.spin_until_future_complete(self, self.future)
        async def srv_finished_checker():
            timeout_sec = 10.0
            while not future.done() and not self.shutting_down and timeout_sec > 0.0:
                await asyncio.sleep(.1)
                timeout_sec -= .1
            is_timeout = timeout_sec <= 0.0
            self.get_logger().warn(f"Service {service} call finished with result: {str(future.result())}{(' TIMEOUT' if is_timeout else '')}")
        await srv_finished_checker()

        return str(future.result())


    # def subscribe_topic(self, topic:str, id_peer:str) -> bool:

    #     if topic in self.topic_read_subscriptions:
    #         if not id_peer in self.topic_read_subscriptions[topic].peers:
    #             self.topic_read_subscriptions[topic].peers.append(id_peer)
    #         return True # we cool here

    #     topic_info = self.discovered_topics_[topic]
    #     if topic_info == None:
    #         return False

    #     msg_type:str = topic_info['msg_types'][0]
    #     message_class = None
    #     try:
    #         message_class = get_message(msg_type)
    #     except:
    #         pass

    #     if message_class == None:
    #         self.get_logger().error(f'NOT subscribing to topic {topic}, msg class {msg_type} not loaded (all={", ".join(topic_info["msg_types"])} )')
    #         return False

    #     is_image = msg_type == 'sensor_msgs/msg/Image'
    #     sub = None
    #     #is_text = msg_type == 'std_msgs/msg/String'
    #     # raw = self.get_parameter_or(f'{topic}.raw', Parameter(name='', value=True)).get_parameter_value().bool_value
    #     if not is_image: #images gave their own node started by FP
    #         # raw = True
    #         reliability = self.get_parameter_or(f'{topic}.reliability', Parameter(name='', value=QoSReliabilityPolicy.BEST_EFFORT)).get_parameter_value().integer_value
    #         durability = self.get_parameter_or(f'{topic}.durability', Parameter(name='', value=DurabilityPolicy.VOLATILE)).get_parameter_value().integer_value

    #         qosProfile = QoSProfile(history=QoSHistoryPolicy.KEEP_LAST, \
    #                                 depth=1, \
    #                                 reliability=reliability, \
    #                                 durability=durability, \
    #                                 lifespan=Infinite \
    #                                 )

    #         self.get_logger().warn(f'Subscribing to topic {topic} {msg_type} {"IMAGE" if is_image else "raw={raw}"}')

    #         sub = self.create_subscription(
    #             msg_type=message_class,
    #             topic=topic,
    #             callback=lambda msg: self.topic_subscriber_callback(topic, is_image, msg),
    #             qos_profile=qosProfile,
    #             raw=True,
    #             callback_group=self.callback_group
    #         )
    #         if sub == None:
    #             self.get_logger().error(f'Failed subscribing to topic {topic}, msg class={msg_type} (all={", ".join(topic_info["msg_types"])} )')
    #             return False

    #     frame_processor = None
    #     # raw_frames_queue = None
    #     make_keyframe_shared = None
    #     make_h264_shared = None
    #     make_v8_shared = None
    #     processed_frames_queue_h264 = None
    #     processed_frames_queue_v8 = None

    #     if is_image: # init topic frame processor thread here
    #         # raw_pipe_in_worker, raw_pipe_in = mp.Pipe(duplex=False)
    #         # raw_frames_queue = mp.Queue(maxsize=20) #queues start throwimg when not consumed
    #         processed_frames_queue_h264 = mp.Queue(5)
    #         processed_frames_queue_v8 = mp.Queue(5)
    #         make_keyframe_shared = mp.Value('b', 1, lock=False)
    #         make_h264_shared = mp.Value('b', 1, lock=False)
    #         make_v8_shared = mp.Value('b', 0, lock=False)
    #         frame_processor = mp.Process(target=ROSFrameProcessor,
    #                                      args=(topic, processed_frames_queue_h264, processed_frames_queue_v8,
    #                                            make_keyframe_shared, make_h264_shared, make_v8_shared, self.get_logger()))

    #     self.topic_read_subscriptions[topic] = TopicReadSubscription(
    #         sub = sub,
    #         peers = [ id_peer ],
    #         frame_processor = frame_processor,
    #         # raw_frames = raw_frames_queue,
    #         make_keyframe_shared = make_keyframe_shared,
    #         make_h264_shared = make_h264_shared,
    #         make_v8_shared = make_v8_shared,
    #         processed_frames_h264 = processed_frames_queue_h264,
    #         processed_frames_v8 = processed_frames_queue_v8,
    #     )

    #     if frame_processor:
    #         frame_processor.start()

    #     return True

    # def unsubscribe_topic(self, topic:str, id_peer:str, out_res_unsubscribed:list[tuple[str,int]]):
    #     if not topic in self.topic_read_subscriptions.keys():
    #         self.get_logger().error(f'{topic} not found in self.topic_read_subscriptions ({str(self.topic_read_subscriptions)})')
    #         return

    #     if id_peer in self.topic_read_subscriptions[topic].peers:
    #         self.topic_read_subscriptions[topic].peers.remove(id_peer)

    #     self.get_logger().info(f'{topic} remaining subs: {len(self.topic_read_subscriptions[topic].peers)} {str(self.topic_read_subscriptions[topic].peers)}')

    #     if len(self.topic_read_subscriptions[topic].peers) == 0: #no subscribers => clear (but keep media streams!)
    #         self.get_logger().warn(f'Unsubscribing from topic {topic} (no subscribers)')
    #         # if (self.topic_read_subscriptions[topic][6]): #stream
    #         #     self.topic_read_subscriptions[topic][6].stop()

    #         if self.topic_read_subscriptions[topic].frame_processor:
    #             self.topic_read_subscriptions[topic].frame_processor.terminate()
    #             self.topic_read_subscriptions[topic].frame_processor.join()

    #         self.destroy_subscription(self.topic_read_subscriptions[topic].sub)
    #         del self.topic_read_subscriptions[topic]

    #         # if topic in self.topic_media_streams_.keys():
    #         #     self.topic_media_streams_[topic].stop()
    #         #     del self.topic_media_streams_[topic]

    #     if id_peer in self.wrtc_peer_read_channels and topic in self.wrtc_peer_read_channels[id_peer]:
    #         self.get_logger().info(f'Peer {id_peer} unsubscribed from {topic} R')
    #         id_closed_dc = self.wrtc_peer_read_channels[id_peer][topic].id
    #         self.wrtc_peer_read_channels[id_peer][topic].close()
    #         self.wrtc_peer_read_channels[id_peer].pop(topic)
    #         out_res_unsubscribed.append([ topic, id_closed_dc ])

    #     # if id_peer in self.wrtc_peer_video_tracks and topic in self.wrtc_peer_video_tracks[id_peer]:
    #     #     self.get_logger().info(f'Peer {id_peer} unsubscribed from {topic} /R IMG (NOT EVEN pausing stream)')
    #     #     # self.wrtc_peer_video_tracks[id_peer][topic].pause()
    #     #     # self.wrtc_peer_video_tracks[id_peer][topic].pause()
    #     #     # id_closed_track = self.wrtc_peer_video_tracks[id_peer][topic].track.id # str generated
    #     #     # if id_peer in self.wrtc_peer_video_tracks:
    #     #     #     if topic in self.wrtc_peer_video_tracks[id_peer]:
    #     #     #         if self.wrtc_peer_video_tracks[id_peer][topic].track:
    #     #     #             self.get_logger().info(f'Peer {id_peer} stopping topic {topic}...')
    #     #     #             await pc.removeTrack(self.wrtc_peer_video_tracks[id_peer][topic])
    #     #     #             # await self.wrtc_peer_video_tracks[id_peer][topic].stop()
    #     #     #             self.get_logger().info(f'Peer {id_peer} stopped topic {topic}...')
    #     #     #         if topic in self.wrtc_peer_video_tracks[id_peer]:
    #     #     #             self.wrtc_peer_video_tracks[id_peer].pop(topic)
    #     #     out_res_unsubscribed.append([ topic ])

    # def topic_subscriber_callback(self, topic, is_image, msg):
    #     self.topic_read_subscriptions[topic].num_received += 1 # num recieved
    #     self.topic_read_subscriptions[topic].last_msg = msg #latest val
    #     self.topic_read_subscriptions[topic].last_msg_time = time.time() #latest time

    #     # if self.is_sio_connected_ and topic != self.data_led_topic and self.data_led != None:
    #     #   self.data_led.once()

    #     log_msg = False
    #     if self.topic_read_subscriptions[topic].num_received == 1: # first data in
    #         self.get_logger().debug(f'Receiving {type(msg).__name__} from {topic}')

    #     if self.topic_read_subscriptions[topic].last_log < 0 or time.time()-self.topic_read_subscriptions[topic].last_log > self.log_message_every_sec:
    #         log_msg = True
    #         self.topic_read_subscriptions[topic].last_log = time.time() #last logged now

    #     if not is_image:
    #         asyncio.get_event_loop().create_task(self.report_data(topic, msg, log=log_msg, total_sent=self.topic_read_subscriptions[topic].num_received))
    #     else:
    #         self.get_logger().error(f'NOT reporting frames for topic {topic} in main proc!')

    #     #     self.report_frame(topic, msg, total_sent=self.topic_read_subscriptions[topic].num_received)
    #         # self.event_loop.create_task(self.report_frame(topic, msg, total_sent=self.topic_read_subscriptions[topic].num_received))

    # def create_local_webrtc_track(self, topic:str):

    #     options = {"framerate": "30", "video_size": "640x480"}
    #     if self.wrtc_relay is None:
    #         if platform.system() == "Darwin":
    #             self.wrtc_webcam = MediaPlayer(
    #                 "default:none", format="avfoundation", options=options
    #             )
    #         elif platform.system() == "Windows":
    #             self.wrtc_webcam = MediaPlayer(
    #                 "video=Integrated Camera", format="dshow", options=options
    #             )
    #         else:
    #             for b in range(3,4):
    #                 for i in range(2,3):
    #                     dev = '/dev/bus/usb/00'+str(b)+'/00'+str(i)
    #                     # try:
    #                     self.wrtc_webcam = MediaPlayer(dev, options=options)
    #                     #     break
    #                     # except:
    #                     #     self.get_logger().error(dev+' failed')

    #         self.wrtc_relay = MediaRelay()

    #     return self.wrtc_relay.subscribe(self.wrtc_webcam.video)

 # async def report_data(self, topic:str, payload:any, log:bool, total_sent:int):

    #     for id_peer in self.wrtc_peer_read_channels.keys():
    #         if topic in self.wrtc_peer_read_channels[id_peer].keys():
    #             # interface_up = {self.is_interface_up('wlan0')}
    #             # self.get_logger().debug(f'Wlan0: {str(interface_up)}')
    #             # if not interface_up:
    #                 # file_logger.error(f'wlan0 down, ignoring...')
    #                 # return
    #             dc = self.wrtc_peer_read_channels[id_peer][topic]
    #             if dc.readyState == 'open':
    #                 if type(payload) is bytes:
    #                     if log:
    #                         self.get_logger().debug(f'△ Sending {len(payload)}B into {topic} for id_peer={id_peer}, total sent: {total_sent}')
    #                     dc.send(payload) #raw
    #                 else:
    #                     if (log):
    #                         self.get_logger().debug(f'△ Sending {type(payload)} into {topic} for id_peer={id_peer}, total sent: {total_sent}')
    #                     dc.send(str(payload)) #raw

    #                 if self.data_led != None:
    #                     self.data_led.once()

    # def report_frame(self, topic:str, frame_msg_bytes:any, total_sent:int):
    #     make_h264 = True #TODO detetct without encoder
    #     make_v8 = False
    #     make_keyframe = False

    #     loopin_start = time.time()
    #     for id_peer in self.wrtc_peer_video_tracks.keys():
    #         if topic in self.wrtc_peer_video_tracks[id_peer].keys():
    #             if self.wrtc_peer_video_tracks[id_peer][topic].track != None:
    #                 sender = self.wrtc_peer_video_tracks[id_peer][topic]
    #                 if (sender.get_send_keyframe(True)): # copy & reset for each sender obj
    #                     make_keyframe = True
    #                 # if isinstance(sender.encoder, Vp8Encoder):
    #                 #     gen_yuv420p = True
    #                 # else:
    #                 #     gen_rgb = True
    #             else:
    #                 self.get_logger().warn(f'No track for {topic} for id_peer={id_peer}, self.wrtc_peer_video_tracks[id_peer][topic]={str(self.wrtc_peer_video_tracks[id_peer][topic])} track={str(self.wrtc_peer_video_tracks[id_peer][topic].track)}')

    #     if make_keyframe:
    #         self.topic_read_subscriptions[topic].make_keyframe.value = 1

    #     # self.get_logger().debug(f'report_frame: Loopin\' took {"{:.5f}".format(time.time() - loopin_start)}s')
    #     # self.get_logger().debug(f'Putting frame into raw_frames of {topic} gen_yuv420p={str(gen_yuv420p)}  gen_rgb={str(gen_rgb)}')
    #     # self.get_logger().info(f'sending frame {str(type(frame_msg_bytes))}')
    #     # raw_pipe = self.topic_read_subscriptions[topic].raw_pipe_in
    #     try:
    #         #raw_pipe.send_bytes(frame_msg_bytes)
    #         self.topic_read_subscriptions[topic].raw_frames.put_nowait(frame_msg_bytes)

    #         # {
    #         #     'fbytes': frame_msg_bytes,
    #         #     'yuv420p': gen_yuv420p,
    #         #     'rgb': gen_rgb
    #         # })
    #         # self.get_logger().info(f'Frame in pipe')
    #     except Full:
    #         self.get_logger().warn(f'Frame processor queue full for {topic} (processing slow)')
    #     # self.get_logger().debug(f'Frame added into raw_frames queue of {topic}')

    # async def report_latest_when_ready(self, id_peer:str, topic:str):

    #     while True:

    #         if not id_peer in self.wrtc_peer_read_channels.keys():
    #             return
    #         if not topic in self.wrtc_peer_read_channels[id_peer].keys():
    #             return
    #         if not topic in self.topic_read_subscriptions.keys():
    #             return #not subscribed yet

    #         payload = self.topic_read_subscriptions[topic].last_msg # saved latest

    #         if payload == None:
    #             return #nothing received yet

    #         dc = self.wrtc_peer_read_channels[id_peer][topic]
    #         if dc.readyState == 'open':
    #             if type(payload) is bytes:
    #                 self.get_logger().debug(f'△ Sending latest {len(payload)}B into {topic} id_peer={id_peer}')
    #                 dc.send(payload) #raw
    #             else:
    #                 self.get_logger().debug(f'△ Sending latest {type(payload)} into {topic} id_peer={id_peer}')
    #                 dc.send(str(payload)) #raw
    #             return
    #         else:
    #             await asyncio.sleep(1) #wait until dc opens


    # async def set_wrtc_answer(self, answer_data:dict):

    #     print('answer data: ', answer_data)

    #     id_peer:str = get_peer_id(answer_data)

    #     if id_peer == None:
    #         return { 'err': 1, 'msg': 'No valid peer id provided' }

    #     if not id_peer in self.wrtc_peers.keys():
    #         return { 'err': 1, 'msg': 'Peer not found' }

    #     if not 'sdp' in answer_data.keys() or not 'type' in answer_data.keys():
    #         return { 'err': 1, 'msg': 'Invalid answer data' }

    #     answer:RTCSessionDescription = RTCSessionDescription(sdp=answer_data['sdp'], type=answer_data['type'])

    #     await self.wrtc_peer_pcs[id_peer].setRemoteDescription(answer)
    #     return { 'success': 1 }

    async def shutdown_cleanup(self):
        self.get_logger().warn('SHUTTING DOWN')
        self.shutting_down = True

        # close peer connections
        peer_remove_coros = [self.remove_peer(peer.id, False) for peer in self.wrtc_peers.values()]
        await asyncio.gather(*peer_remove_coros)

        await self.sio.disconnect()
        self.spin_task.cancel()

        if self.sio_wait_task != None:
            self.sio_wait_task.cancel()
            await self.sio_wait_task

        if self.sio_reconnect_wait_task != None:
            await self.sio_reconnect_wait_task

        if self.conn_led != None or self.data_led != None:
            if self.conn_led != None:
                self.conn_led.clear()
            if self.data_led != None:
                self.data_led.clear()
            #TODO actually I should spin ros node some more here
            await asyncio.sleep(1) # wait a bit

    async def spin_async(self, rcl_executor:rclpy.executors.Executor, ctx:rclpy.context.Context, cbg:rclpy.callback_groups.CallbackGroup):

        # cancel = self.create_guard_condition(lambda: None)

        # while ctx.ok() and not self.shutting_down:
        #     try:
        #         asyncio.get_event_loop().run_in_executor(None, rcl_executor.spin_once)
        #     except Exception as e:
        #         print(c(f'Exception while spinning ctrl node', 'red'))
        #         traceback.print_exception(e)
        #         pass
        #     await asyncio.sleep(0.01) #slow spin
        print('Done spinning ctrl node')

        # def _spin(future: asyncio.Future, event_loop: asyncio.AbstractEventLoop):
        #     while ctx.ok() and not future.cancelled() and not self.shutting_down:
        #         try:
        #             print('**spin**')
        #             executor.spin_once()
        #         except:
        #             pass
        #     if not future.cancelled():
        #         event_loop.call_soon_threadsafe(future.set_result, None)


        # print('**about to spin**')
        # await _spin()

        # node.spin_task = asyncio.get_event_loop().create_future()
        # node.spin_thread = threading.Thread(target=_spin, args=(node, node.spin_task, asyncio.get_event_loop()))
        # node.spin_thread.start()
        # try:
        #     await _spin()
        # except:
        #     pass

        # cancel.trigger()

        # node.spin_thread.join()
        # node.destroy_guard_condition(cancel)

# async def main_async(bridge_node:BridgeController, executor:rclpy.executors.Executor, ctx:rclpy.context.Context, cbg:rclpy.callback_groups.CallbackGroup):
    # create a node without any work to do

async def main_async():

    # reader runs on a separate process
    reader_on_flag = mp.Value('b', 1, lock=False)
    reader_ctrl_queue = mp.Queue()
    reader_out_queue = mp.Queue()
    topic_read_processor = mp.Process(target=TopicReadProcessor,
                                      args=(reader_on_flag, reader_ctrl_queue, reader_out_queue, None))
    topic_read_processor.start()

    rcl_ctx = Context()
    rcl_ctx.init() # This must be done before any ROS nodes can be created.

    # rclpy.init(context=rcl_ctx)
    # rclpy.uninstall_signal_handlers() #duplicate?
    # ctx.init()

    rcl_cbg = MutuallyExclusiveCallbackGroup()

    rcl_executor = rclpy.executors.SingleThreadedExecutor(context=rcl_ctx)

    # rclpy.init(context=rcl_ctx)

    bridge_node = BridgeController(context=rcl_ctx, cbg=rcl_cbg, reader_ctrl_queue=reader_ctrl_queue, reader_out_queue=reader_out_queue)

    rcl_executor.add_node(bridge_node)
    rcl_cbg.add_entity(bridge_node)
    rcl_cbg.add_entity(rcl_ctx)
    rcl_cbg.add_entity(rcl_executor)

    try:
        # create tasks for spinning and sleeping
        spin_task = asyncio.get_event_loop().create_task(bridge_node.spin_async(rcl_executor, rcl_ctx, rcl_cbg))
        sio_task = asyncio.get_event_loop().create_task(bridge_node.spin_sio_client(addr=bridge_node.sio_address, port=bridge_node.sio_port, path=bridge_node.sio_path))

        asyncio.get_event_loop().create_task(bridge_node.read_queued_data())
        asyncio.get_event_loop().create_task(bridge_node.introspection.start())

    except Exception as e:
        print(c('Exception in main_async()', 'red'))
        traceback.print_exc(e)

    # concurrently execute both tasks on this process
    await asyncio.wait([spin_task, sio_task], return_when=asyncio.ALL_COMPLETED)

    # cancel tasks
    if spin_task.cancel():
        await spin_task
    if sio_task.cancel():
        await sio_task

    # try:
    #     asyncio.get_event_loop().run_until_complete(main_async(bridge_node, executor, ctx, cbg))
    # except:
    #     pass
    reader_on_flag.value = 0 #stop readed thread
    topic_read_processor.terminate()
    topic_read_processor.join()

    asyncio.get_event_loop().run_until_complete(bridge_node.shutdown_cleanup())

    try:
        bridge_node.destroy_node()
        rcl_executor.shutdown()
        rclpy.shutdown()
    except:
        pass

    asyncio.get_event_loop().close()

def main(): # ros2 calls this, so init here
    # print(f'mp.start_method={mp.get_start_method()}')
    try:
        asyncio.run(main_async())
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass

if __name__ == '__main__': #ignired by ros
    main()