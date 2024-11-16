import rclpy
from rclpy.node import Node, Parameter, QoSProfile, Publisher
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy
from rclpy.duration import Duration, Infinite
from rclpy.serialization import deserialize_message
from rclpy_message_converter import message_converter
from rcl_interfaces.msg import Parameter, ParameterValue, ParameterType
from rcl_interfaces.msg import ParameterDescriptor, ParameterType, FloatingPointRange, IntegerRange
from rclpy_message_converter import message_converter
import concurrent.futures
import gpiod

from dulwich.repo import Repo
from dulwich import porcelain

from rclpy.executors import SingleThreadedExecutor, MultiThreadedExecutor

from .inc.status_led import StatusLED
from termcolor import colored as c

import subprocess
import signal

import fractions
import tarfile, io
import yaml
import xml.etree.ElementTree as XmlET

# from rcl_interfaces.msg import ParameterDescriptor
# import signal
import time
# import sys
import traceback
# import netifaces
import uuid 
import os

import time

from .inc.ros_video_streaming import ImageTopicReadSubscription, CameraVideoStreamTrack, IsImageType, IsEncodedStreamType

# from rclpy.subscription import TypeVar
from rosidl_runtime_py.utilities import get_message, get_interface

import asyncio
import multiprocessing as mp
from queue import Empty, Full
import selectors

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
from .inc.topic_processor_worker import TopicProcessorWorker
from .inc.topic_writer import TopicWritePublisher
from .inc.peer import WRTCPeer

from .inc.introspection import Introspection
from .inc.config import BridgeControllerConfig

import subprocess

import docker
docker_client = None
try:
    host_docker_socket = 'unix:///host_run/docker.sock' # link /var/run/ to /host_run/ in docker-compose
    # host_docker_socket = 'tcp://0.0.0.0:2375'
    docker_client = docker.DockerClient(base_url=host_docker_socket)
except Exception as e:
    print(c(f'Failed to init docker client with {host_docker_socket} {e}', 'red'))
    pass

ROOT = os.path.dirname(__file__)

class BridgeController(Node, BridgeControllerConfig):
    ##
    # node constructor
    ##
    def __init__(self, context, executor):
        super().__init__(node_name='phntm_bridge',
                         context=context,
                         enable_rosout=True,
                         use_global_arguments=True)
        
        self.rcl_executor = executor
        self.shutting_down:bool = False
        # self.paused:bool = False
       
        self.get_logger().set_level(rclpy.logging.LoggingSeverity.DEBUG)
        self.load_config(self.get_logger())

        self.ros_distro = os.environ["ROS_DISTRO"]
        self.get_logger().debug(f'ROS Distro is: {self.ros_distro}')
        
        git_repo = Repo('.')
        self.git_head_sha = git_repo.head().decode()
        git_tags = git_repo.refs.as_dict(b"refs/tags")
        self.latest_git_tag = None
        for tag, sha in git_tags.items():
            if sha.decode() != self.git_head_sha:
                continue
            self.latest_git_tag = tag.decode()
        self.get_logger().debug(f"Git commit: {self.git_head_sha} Tag: {self.latest_git_tag}")
        
        # separate process
        self.topic_read_subscriptions:dict[str: TopicReadSubscription] = {}
        self.image_topic_read_subscriptions:dict[str: ImageTopicReadSubscription] = {}

        # this process
        self.topic_write_publishers:dict[str: TopicWritePublisher] = {}

        self.service_clients:dict[str: any] = {} # service name => client
        
        self.wrtc_peers:dict[str: WRTCPeer] = {}

        self.spin_thread: threading.Thread = None
        self.spin_task: asyncio.Future[any] = None
        self.sio_wait_task: asyncio.Future[any] = None
        self.sio_reconnect_wait_task: asyncio.Future[any] = None


    def start(self, video_worker_ctrl_queue:mp.Queue=None, video_worker_out_queue:mp.Queue=None, image_worker_ctrl_queue:mp.Queue=None, image_worker_out_queue:mp.Queue=None, data_worker_ctrl_queue:mp.Queue=None):
        self.create_sio_client()

        discovery_period:float = self.get_parameter('discovery_period_sec').get_parameter_value().double_value
        stop_discovery_after:float = self.get_parameter('stop_discovery_after_sec').get_parameter_value().double_value
        self.introspection:Introspection = Introspection(period=discovery_period,
                                                         stop_after=stop_discovery_after,
                                                         ctrl_node=self,
                                                         sio=self.sio)

        self.setup_conn_leds()

        self.video_worker_ctrl_queue:mp.Queue = video_worker_ctrl_queue
        self.video_worker_out_queue:mp.Queue = video_worker_out_queue
        self.image_worker_ctrl_queue:mp.Queue = image_worker_ctrl_queue
        self.image_worker_out_queue:mp.Queue = image_worker_out_queue
        self.data_worker_ctrl_queue:mp.Queue = data_worker_ctrl_queue
        
        self.image_read_executor = concurrent.futures.ThreadPoolExecutor(max_workers=10, thread_name_prefix=f'im_read')

        self.get_logger().debug(f'Phntm Bridge started, idRobot={c(self.id_robot, "cyan")}')
    
    
    def setup_conn_leds(self):
        self.conn_led = None
        self.data_led = None
        if (self.conn_led_pin > -1 or self.data_led_pin > -1) and self.conn_led_gpio_chip: # contril via pins
            led_pin_config = {}
            if self.conn_led_pin > -1:
                led_pin_config[self.conn_led_pin] = gpiod.LineSettings(
                    direction=gpiod.line.Direction.OUTPUT, output_value=gpiod.line.Value.INACTIVE
                )
            if self.data_led_pin > -1:
                led_pin_config[self.data_led_pin] = gpiod.LineSettings(
                    direction=gpiod.line.Direction.OUTPUT, output_value=gpiod.line.Value.INACTIVE
                )
            self.led_pin_request = None
            try:
                self.led_pin_request = gpiod.request_lines(
                    self.conn_led_gpio_chip,
                    consumer="bridge-activity",
                    config=led_pin_config,
                )
            except Exception as e:
                self.get_logger().error(f'Error requesting GPIO for status leds: {e}')
            if self.led_pin_request:
                try:
                    if self.conn_led_pin > -1:
                        self.get_logger().info(f'CONN Led uses pin {self.conn_led_pin}')
                        self.conn_led = StatusLED('conn', node=self, mode=StatusLED.Mode.OFF, pin=self.conn_led_pin, pin_request=self.led_pin_request)
                        self.conn_led.set_fast_pulse()
                except Exception as e:
                    self.get_logger().error(f'Error initializing connection led: {e}')
                try:
                    if self.data_led_pin > -1:
                        self.get_logger().info(f'DATA Led uses pin {self.data_led_pin}')
                        self.data_led = StatusLED('data', node=self, mode=StatusLED.Mode.OFF, pin=self.data_led_pin, pin_request=self.led_pin_request)
                except Exception as e:
                    self.get_logger().error(f'Error initializing data led: {e}')
        else: # control via topics
            if (self.conn_led_topic != None and self.conn_led_topic != ''):
                self.get_logger().info(f'CONN Led uses {self.conn_led_topic}')
                self.conn_led = StatusLED('conn', node=self, mode=StatusLED.Mode.OFF, topic=self.conn_led_topic, qos=QoSProfile(depth=1, reliability=QoSReliabilityPolicy.BEST_EFFORT))
                self.conn_led.set_fast_pulse()
            
            if (self.data_led_topic != None and self.data_led_topic != ''):
                self.get_logger().info(f'DATA Led uses {self.data_led_topic}')
                self.data_led = StatusLED('data', node=self, mode=StatusLED.Mode.OFF, topic=self.data_led_topic, qos=QoSProfile(depth=1, reliability=QoSReliabilityPolicy.BEST_EFFORT))
    
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
            self.get_logger().debug('Socket.io connection established, auth successful')

            self.sio.connected = True #socket.io sets this after callback, makes report calls work immediately

            if self.conn_led != None:
                self.conn_led.on()

            # push latest introspection results to the server
            asyncio.get_event_loop().create_task(self.introspection.report_idls())
            asyncio.get_event_loop().create_task(self.introspection.report_nodes())
            asyncio.get_event_loop().create_task(self.introspection.report_topics())
            asyncio.get_event_loop().create_task(self.introspection.report_services())
            asyncio.get_event_loop().create_task(self.introspection.report_docker())
            asyncio.get_event_loop().create_task(self.introspection.report_introspection())

        event_loop = asyncio.get_event_loop()

        @self.sio.on('peer')
        async def on_peer(data):
            id_peer:str = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            return await self.on_peer_connect(id_peer, data)

        @self.sio.on('introspection')
        async def on_introspection(data):
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
            return { 'success': 1, 'introspection': self.introspection.running }

        # subscribe topics
        @self.sio.on('subscribe')
        async def on_subscribe(data:dict):
            id_peer = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            if not id_peer in self.wrtc_peers:
                return { 'err': 2, 'msg': 'Peer id '+id_peer+' not found here' }
            peer:WRTCPeer = self.wrtc_peers[id_peer]
            if not peer:
                return { 'err': 2, 'msg': 'Peer not connected' }
            if not 'sources' in data:
                self.get_logger().error(f'No subscribe sources specified by {peer}')
                return { 'err': 2, 'msg': 'No topics specified' }

            for src in data['sources']:
                if not src in peer.read_subs:
                    peer.read_subs.append(src)

            await self.process_peer_subscriptions(peer, send_update=True)

        # unsubscribe topics
        @self.sio.on('unsubscribe')
        async def on_unsubscribe(data:dict):
            id_peer = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            peer:WRTCPeer = self.wrtc_peers[id_peer]
            if not peer:
                return { 'err': 2, 'msg': 'Peer not connected' }
            if not 'sources' in data:
                self.get_logger().error(f'No unsubscribe sources specified by {peer}')
                return { 'err': 2, 'msg': 'No topics specified' }

            for src in data['sources']:
                if src in peer.read_subs:
                    peer.read_subs.remove(src)

            await self.process_peer_subscriptions(peer, send_update=True)

        # WRITE SUBS
        @self.sio.on('subscribe:write')
        async def on_subscribe_write(data:dict):
            id_peer = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            peer:WRTCPeer = self.wrtc_peers[id_peer]
            if not peer:
                return { 'err': 2, 'msg': 'Peer not connected' }

            res = []
            for src in data['sources']:
                topic = src[0]
                msg_type = src[1]
                topic_active = False
                for sub in peer.write_subs: 
                    if sub[0] == topic: # already exists
                        topic_active = True
                        id_dc = peer.inbound_data_channels[topic].id
                        res.append([topic, id_dc, msg_type])
                        break
                if not topic_active: # open new
                    peer.write_subs.append([topic, msg_type])
                    id_dc = await self.open_write_channel(topic, msg_type, peer)
                    res.append([topic, id_dc, msg_type])

            return { 'write_data_channels': res }
            # await self.process_peer_subscriptions(peer, send_update=True)

        # CLOSE WRITE SUBS
        @self.sio.on('unsubscribe:write')
        async def on_unsubscribe_write(data:dict):
            id_peer = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            peer:WRTCPeer = self.wrtc_peers[id_peer]
            if not peer:
                return { 'err': 2, 'msg': 'Peer not connected' }

            for sub in list(peer.write_subs): #copy
                if sub[0] in data['sources']:
                    peer.write_subs.remove(sub)

            await self.process_peer_subscriptions(peer, send_update=True)

        @self.sio.on('sdp:answer')
        async def on_sdp_answer(data:dict):
            id_peer = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            if not id_peer in self.wrtc_peers.keys():
                return { 'err': 2, 'msg': 'Peer not connected' }
            peer:WRTCPeer = self.wrtc_peers[id_peer]
            if not peer:
                self.get_logger().error(f'Peer not connected in on_read_subscriptions_change, peer={id_peer}')
                return { 'err': 2, 'msg': 'Peer not connected' }
            if not 'sdp' in data.keys():
                return { 'err': 2, 'msg': 'No SDP provided' }

            if peer.pc.signalingState != 'have-local-offer':
                self.get_logger().error(f'Not setting SDP answer from peer {id_peer}, signalingState={peer.pc.signalingState}')
                return

            answer = RTCSessionDescription(sdp=data['sdp'], type='answer')
            self.get_logger().debug(c(f'Setting SDP answer from peer {id_peer}, signalingState={peer.pc.signalingState}', 'cyan'))
            if self.get_parameter('log_sdp').get_parameter_value().bool_value:
                self.get_logger().info(c(data['sdp'], 'dark_grey'))
            await peer.pc.setRemoteDescription(answer)

            return { 'success': 1 }

        # SERVICE CALLS
        @self.sio.on('service')
        async def on_peer_service_call(data:dict):
            id_peer = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            if not id_peer in self.wrtc_peers.keys():
                return { 'err': 2, 'msg': 'Peer not connected' }
            return await self.on_service_call(id_peer, data, event_loop)

        @self.sio.event
        async def connect_error(data):
            self.get_logger().error('Socket.io connection failed: ' + str(data))

        @self.sio.on('peer:disconnected')
        async def on_peer_disconnected(data:dict):
            id_peer = WRTCPeer.GetId(data)
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }
            if not id_peer in self.wrtc_peers.keys():
                return { 'err': 2, 'msg': 'Peer was not connected' }
            self.wrtc_peers[id_peer].sio_connected = False
            self.get_logger().warn(f'Peer {id_peer} disconnected from Socket.io server (clearing webrtc)')
            await self.remove_peer(id_peer, False)

        @self.sio.on('file')
        async def on_file_request(data):
            file_url:str = data
            pkg:str = None
            pkg_prefix = ""
            
            if file_url.startswith('file:/'):
                file_url = file_url.replace('file://', '')
                file_url = file_url.replace('file:/', '')
                if not file_url.startswith('/'):
                    file_url = '/' + file_url
                self.get_logger().warn(f'Bridge requesting file {file_url}')
                
            elif file_url.startswith('package:/'):
                file_url = file_url.replace('package://', '')
                file_url = file_url.replace('package:/', '')
                
                parts = file_url.split('/')
                pkg = parts[0]
                
                # file_url = file_url.replace(f'{pkg}/', '')
                
                if not file_url.startswith('/'):
                    file_url = '/' + file_url
                
                self.get_logger().warn(f'Bridge requesting file in pkg {pkg}: {file_url}')
                
                if pkg is not None:
                    res = subprocess.run([f"/opt/ros/{self.ros_distro}/bin/ros2", "pkg", "prefix", pkg], capture_output=True)
                    if res.stdout:
                        pkg_prefix = res.stdout.decode("ASCII").rstrip() + '/share'
                        self.get_logger().info(f'local ros2 {self.ros_distro} pkg prefix is {pkg_prefix}')
                    else:
                        self.get_logger().info(f'local ros2 {self.ros_distro} pkg prefix for {pkg} not found in this fs')
            else:
                self.get_logger().warn(f'Bridge requesting invalid file {file_url}')
                return None # file not found
            
            if pkg_prefix and os.path.isfile(pkg_prefix + file_url):
                self.get_logger().info(f'File found in this fs (pkg_prefix={pkg_prefix})')
                f = open(file_url, "rb")
                res = f.read()
                f.close()
                return res
            elif os.path.isfile(file_url):
                self.get_logger().info(f'File found in this fs')
                f = open(file_url, "rb")
                res = f.read()
                f.close()
                return res
            elif docker_client:
                self.get_logger().info(f'File not found in this fs, searching docker containers...')
                docker_containers = docker_client.containers.list(all=False)
                for container in docker_containers:
                    pkg_prefix = ""    
                    if pkg:
                        cmd = f'/bin/bash -c "export PS1=phntm && . /opt/ros/{self.ros_distro}/setup.bash && . ~/.bashrc && /opt/ros/{self.ros_distro}/bin/ros2 pkg prefix {pkg}"'
                        res = container.exec_run(cmd)
                        if res.exit_code == 1:
                            self.get_logger().info(f'pkg not found in cont {container.name} \nout={res.output}\ncmd={cmd}')
                        else:
                            pkg_prefix = res.output.decode("ASCII").rstrip() + '/share'
                            self.get_logger().warn(f'cont {container.name} has pkg in {pkg_prefix}')
                    
                    try:
                        tar_chunks, stats = container.get_archive(pkg_prefix+file_url, chunk_size=None, encode_stream=False)
                    except Exception as e:
                        self.get_logger().info(f'File not found in {container.name} fs')
                        continue
                    
                    self.get_logger().debug(f'File found in {container.name} fs')
                    self.get_logger().debug(str(stats))
                    
                    b_arr = []
                    for chunk in tar_chunks:
                        b_arr.append(chunk)
                    
                    tar_bytes = b''.join(b_arr)
                    
                    self.get_logger().info(f' making tar obj w {len(tar_bytes)} B')
                    
                    file_like_object = io.BytesIO(tar_bytes)
                    tar = tarfile.open(fileobj=file_like_object)

                    self.get_logger().info(f' Tar memebers: {tar.getnames()}')

                    member = tar.getmember(stats['name'])
                    
                    self.get_logger().info(f' {member} data starts at {member.offset_data}')
                    
                    res = tar_bytes[member.offset_data:member.offset_data+stats['size']]
                  
                    self.get_logger().info(f' Returning {len(res)} B of tar member {member}')
                    return res
                      
            return None # file not found

        @self.sio.on('*')
        async def catch_all(event, data):
            self.get_logger().warn('Unhandled: Socket.io event ' + str(event) + ' with ' + str(data))

        @self.sio.event
        async def connect_error(data):
            self.get_logger().error('Socket.io connection failed')
            await self.remove_all_peers()
            await self.reset_conn_leds()

        @self.sio.event
        async def disconnect():
            self.get_logger().warn('Socket.io disconnected from server')
            await self.remove_all_peers()
            await self.reset_conn_leds()

    ##
    # spin socket.io
    ##
    async def spin_sio_client(self):
        while not self.shutting_down:
            try:
                self.get_logger().info(f'Socket.io connecting to {self.sio_address}:{self.sio_port}{self.sio_path}')
                auth_data = {
                    'id_robot': self.id_robot,
                    'key': self.auth_key,
                    'name': self.get_parameter('name').get_parameter_value().string_value,
                    'ros_distro': self.ros_distro,
                    'git_sha': self.git_head_sha,
                    'git_tag': self.latest_git_tag
                }
                await self.sio.connect(url=f'{self.sio_address}:{self.sio_port}', socketio_path=self.sio_path, auth=auth_data)

                self.sio_wait_task = asyncio.get_event_loop().create_task(self.sio.wait()) # wait as long as connected
                await self.sio_wait_task
                self.sio_wait_task = None
            except socketio.exceptions.ConnectionError:
                self.get_logger().warn(f'Socket.io connection error, retrying in {self.sio_connection_retry_sec}s...')
                self.sio_reconnect_wait_task = asyncio.get_event_loop().create_task(asyncio.sleep(self.sio_connection_retry_sec))
                await self.sio_reconnect_wait_task
                self.sio_reconnect_wait_task = None
            except asyncio.CancelledError:
                self.get_logger().info('Socket.io CancelledError')
                return

    ##
    # init p2p connection with a peer sdp offer
    ##
    async def on_peer_connect(self, id_peer:str, peer_data:dict):

        self.get_logger().debug(c(f'Peer {id_peer} connected... w peer_data={peer_data}', 'magenta'))

        peer = WRTCPeer(id_peer=id_peer,
                        id_app=peer_data['id_app'] if 'id_app' in peer_data.keys() else None,
                        id_instance=peer_data['id_instance'] if 'id_instance' in peer_data.keys() else None,
                        session=uuid.uuid4(),    
                        ctrl_node=self,
                        ice_servers=self.ice_servers,
                        ice_username=self.ice_username,
                        ice_credential=self.ice_credential)

        @peer.pc.on("connectionstatechange")
        async def on_connectionstatechange():
            if peer.pc.connectionState == "failed":
                self.get_logger().warn(f'{peer} connection state is failed')
                await self.remove_peer(peer.id, wait=True)

        self.wrtc_peers[id_peer] = peer
        peer.read_subs = peer_data['read'] if 'read' in peer_data.keys() else []
        peer.write_subs = peer_data['write'] if 'write' in peer_data.keys() else []

        return await self.process_peer_subscriptions(peer, send_update=False, ui_config=True)


    async def process_peer_subscriptions(self, peer:WRTCPeer, send_update=False, ui_config=False) -> dict: # send_update=False => return res
        
        if not await self.peer_processing_state_checker(peer):
            self.get_logger().error(f'Failed to process {peer} subs, peer busy. read={peer.read_subs} write={peer.write_subs}; signalingState={peer.pc.signalingState} iceGatheringState={peer.pc.iceGatheringState}')
            return None
        
        peer.processing_subscriptions = True 
        disconnected = peer.pc.connectionState == "failed" or not peer.sio_connected or self.shutting_down
        
        self.get_logger().info(f'Processing {"disconnected " if disconnected else ""}{peer} subs, read={peer.read_subs} write={peer.write_subs}; signalingState={peer.pc.signalingState} iceGatheringState={peer.pc.iceGatheringState}')

        res = {
            'session': peer.session.hex,
            'read_video_streams': [],
            'read_data_channels': [],
            'write_data_channels': [],
        }
        
        if ui_config: # = on conect only, adding config extras for the client
            
            collapse_services = self.get_parameter('collapse_services').get_parameter_value().string_array_value
            if len(collapse_services) == 1 and collapse_services[0] == '':
                collapse_services = []
            
            res['input_drivers'] = self.input_drivers
            res['input_defaults'] = self.input_defaults # pass input mappings & service buttons
            res['ui'] = {
                'battery_topic': self.get_parameter('ui_battery_topic').get_parameter_value().string_value,
                'docker_control': self.docker_control_enabled,
                'docker_monitor_topic': self.get_parameter('docker_monitor_topic').get_parameter_value().string_value,
                'wifi_monitor_topic': self.get_parameter('ui_wifi_monitor_topic').get_parameter_value().string_value,
                'enable_wifi_scan': self.get_parameter('ui_enable_wifi_scan').get_parameter_value().bool_value,
                'enable_wifi_roam': self.get_parameter('ui_enable_wifi_roam').get_parameter_value().bool_value,
                'collapse_services': collapse_services,
                'collapse_unhandled_services': self.get_parameter('collapse_unhandled_services').get_parameter_value().bool_value
            }

        peer.topics_not_discovered = []
        for sub in peer.read_subs:
            if sub in self.introspection.discovered_topics.keys():
                msg_type = self.introspection.discovered_topics[sub]['msg_type']
                is_image = IsImageType(msg_type)
                
                try: 
                    self.declare_parameter(f'{sub}.reliability', 0) # 0 = best effort, 1 = reliable
                    self.declare_parameter(f'{sub}.durability', 0) # 0 system default, 1 = transient local, 2 = volatile
                    self.declare_parameter(f'{sub}.lifespan_sec', -1) # num sec as int, -1 infinity
                except rclpy.exceptions.ParameterAlreadyDeclaredException:
                    pass
                reliability = self.get_parameter(f'{sub}.reliability').get_parameter_value().integer_value
                durability = self.get_parameter(f'{sub}.durability').get_parameter_value().integer_value
                lifespan = self.get_parameter(f'{sub}.lifespan_sec').get_parameter_value().integer_value
            
                qosProfile = QoSProfile(
                    history=QoSHistoryPolicy.KEEP_LAST,
                    depth=1,
                    reliability=reliability,
                    durability=durability,
                    lifespan=Infinite if lifespan < 0 else Duration(seconds=lifespan)
                )
                    
                if not is_image:
                    id_dc = await self.subscribe_data_topic(sub, qos=qosProfile, peer=peer)
                    topic_conf = {} # passing config extras to the UI
                    # if sub in self.topic_overrides:
                    match msg_type:
                        case 'vision_msgs/msg/Detection2DArray' | 'vision_msgs/msg/Detection3DArray':
                            # NN stuffs
                            try: 
                                self.declare_parameter(f'{sub}.nn_input_w', 416)
                                self.declare_parameter(f'{sub}.nn_input_h', 416)
                                self.declare_parameter(f'{sub}.nn_detection_labels', [ '' ]) # nn class labels
                            except rclpy.exceptions.ParameterAlreadyDeclaredException:
                                pass
                            topic_conf['nn_input_w'] = self.get_parameter(f'{sub}.nn_input_w').get_parameter_value().integer_value
                            topic_conf['nn_input_h'] = self.get_parameter(f'{sub}.nn_input_h').get_parameter_value().integer_value
                            topic_conf['nn_detection_labels'] = self.get_parameter(f'{sub}.nn_detection_labels').get_parameter_value().string_array_value
                        case 'sensor_msgs/msg/CameraInfo':
                            try: 
                                self.declare_parameter(f'{sub}.frustum_color', 'cyan')
                                self.declare_parameter(f'{sub}.frustum_near', 0.01)
                                self.declare_parameter(f'{sub}.frustum_far', 1.0)
                                self.declare_parameter(f'{sub}.force_frame_id', '')
                            except rclpy.exceptions.ParameterAlreadyDeclaredException:
                                pass
                            topic_conf['frustum_color'] = self.get_parameter(f'{sub}.frustum_color').get_parameter_value().string_value
                            topic_conf['frustum_near'] = self.get_parameter(f'{sub}.frustum_near').get_parameter_value().double_value
                            topic_conf['frustum_far'] = self.get_parameter(f'{sub}.frustum_far').get_parameter_value().double_value
                            force_frame_id = self.get_parameter(f'{sub}.force_frame_id').get_parameter_value().string_value
                            if force_frame_id:
                                topic_conf['force_frame_id'] = force_frame_id
                        case 'sensor_msgs/msg/BatteryState':
                            # Battery
                            try:
                                self.declare_parameter(f'{sub}.min_voltage', 0.0)
                                self.declare_parameter(f'{sub}.max_voltage', 10.0)
                            except rclpy.exceptions.ParameterAlreadyDeclaredException:
                                pass
                            topic_conf['min_voltage'] = self.get_parameter(f'{sub}.min_voltage').get_parameter_value().double_value
                            topic_conf['max_voltage'] = self.get_parameter(f'{sub}.max_voltage').get_parameter_value().double_value
                    res['read_data_channels'].append([sub, id_dc, msg_type, reliability == QoSReliabilityPolicy.RELIABLE, topic_conf])
                elif is_image:
                    id_track = await self.subscribe_image_topic(sub, qos=qosProfile, peer=peer)
                    res['read_video_streams'].append([ sub, id_track ])

            else: #topic not discovered yet
                self.get_logger().info(c(f'{peer} missing {sub}, not discovered yet', 'dark_grey'))
                if not sub in peer.topics_not_discovered:
                    peer.topics_not_discovered.append(sub) # introspection will keep running

        if not disconnected and len(peer.topics_not_discovered) > 0:
            self.introspection.add_waiting_peer(peer)
            asyncio.get_event_loop().create_task(self.introspection.start())
        else:
            self.introspection.remove_waiting_peer(peer)

        # open write data channels
        for sub in peer.write_subs:
            topic = sub[0]
            msg_type = sub[1]
            id_dc = await self.open_write_channel(topic, msg_type, peer)
            res['write_data_channels'].append([ topic, id_dc, msg_type ]) # topic, msg_type, channel

        # unsubscribe from data channels
        for topic in list(peer.outbound_data_channels.keys()):
            if not topic in peer.read_subs:
                self.get_logger().info(f'{peer} unsubscribing from {topic}')
                await self.unsubscribe_data_topic(topic, peer=peer, msg_callback=None)
                res['read_data_channels'].append([ topic ]) # no id => unsubscribed

        # unsubscribe from video streams
        for sub in list(peer.video_tracks.keys()):
            if not sub in peer.read_subs:
                self.get_logger().info(f'{peer} unsubscribing from image topic {sub}')
                await self.unsubscribe_image_topic(sub, peer)
                res['read_video_streams'].append([ sub ]) # no id => unsubscribed

        # close write channels
        for topic in list(peer.inbound_data_channels.keys()):
            topic_active = False
            for sub in peer.write_subs:
                if sub[0] == topic:
                    topic_active = True
                    break
            if not topic_active:
                self.get_logger().info(f'{peer} stopped writing into {topic}')
                await self.close_write_channel(topic, peer)
                res['write_data_channels'].append([ topic ]) # no id => unsubscribed

        if disconnected:
            peer.processing_subscriptions = False
            return True #all cleanup done

        if len(res['read_video_streams']) == 0 and len(res['read_data_channels']) == 0 and len(res['write_data_channels']) == 0:
            self.get_logger().debug(c(f'{peer} connected, nothing else to do here', 'dark_grey'))
            peer.processing_subscriptions = False
            res['success'] = 1
            return res  # SDF can't be generated with no channels

        if not await self.peer_signalling_stable_checker(peer):
            peer.processing_subscriptions = False
            if send_update:
                return None
            else:
                return { 'err': 2, 'msg': 'Timed out waiting for stable signalling state' }

        self.get_logger().debug(c(f'Creating SDP offer for {peer}...', 'cyan'))
        offer = await peer.pc.createOffer()
        await peer.pc.setLocalDescription(offer)

        if not await self.peer_ice_checker(peer):
            peer.processing_subscriptions = False
            if send_update:
                return None
            else:
                return { 'err': 2, 'msg': 'Timed out waiting for ICE gathering state' }

        if not peer.sio_connected and peer.pc.connectionState != "failed":
            self.get_logger().error(f'{peer} sio_connected={peer.sio_connected}; pc.connectionState={peer.pc.connectionState}')
            peer.processing_subscriptions = False
            return False

        if self.get_parameter('log_sdp').get_parameter_value().bool_value:
            self.get_logger().info(c(peer.pc.localDescription.sdp, 'dark_grey'))

        res['offer'] = peer.pc.localDescription.sdp

        if not send_update:
            # self.get_logger().debug(c(f'Returning update for {peer}: {str(res)}', 'cyan'))
            peer.processing_subscriptions = False
            return res
        else:
            # self.get_logger().debug(c(f'Pushing update for {peer}: {str(res)}', 'cyan'))
            return await self.update_peer(peer, res)


    async def update_peer(self, peer:WRTCPeer, update_data):
        self.get_logger().info(f'Sending update to {peer}')
        if peer.id_app:
            update_data['id_app'] = peer.id_app
        if peer.id_instance:
            update_data['id_instance'] = peer.id_instance
        print(update_data)
        await self.sio.emit(event='peer:update',
                            data=update_data,
                            callback=peer.on_answer_reply)


    # WRTC peer disconnected
    # optionally wait a bit for reconnect, then clean up
    async def remove_peer(self, id_peer:str, wait:bool=True):
        if not id_peer in self.wrtc_peers.keys():
            return

        peer = self.wrtc_peers[id_peer]

        # if wait:
        #     wait_s = 2.0
        #     self.get_logger().info(f'{peer} seems to be disconnected (waiting {wait_s}s...)')

        #     # self.paused = True
        #     await asyncio.sleep(wait_s) #wait a bit for reconnects (?!)

        #     if not id_peer in self.wrtc_peers.keys():
        #         return # already removed

        #     peer = self.wrtc_peers[id_peer]
        #     if peer.pc.connectionState in [ 'connected' ]:
        #         self.get_logger().info(f'{peer} recovered, we good')
        #         return

        self.get_logger().info(c(f'{peer} disconnected, cleaning up', 'red'))

        self.introspection.remove_waiting_peer(peer)
        peer.read_subs = []
        peer.write_subs = []

        await self.process_peer_subscriptions(peer, send_update=False) #unsubscribes
        
        self.get_logger().info(c(f'{peer} subscriptions clear', 'red'))
        
        if id_peer in self.wrtc_peers.keys():
            try:
                if not self.shutting_down:
                    await self.wrtc_peers[id_peer].pc.close()
            except Exception as e:
                self.get_logger().info(c(f'Exception while closing pc of {peer}, {e}', 'red'))
                pass
            del self.wrtc_peers[id_peer]
    
    
    async def remove_all_peers(self):
        if len(self.wrtc_peers.values()) == 0:
            return # we cool
        peer_remove_coros = [self.remove_peer(peer.id, False) for peer in self.wrtc_peers.values()]
        print(c(f'Disconnecting {len(self.wrtc_peers)} peers...', 'cyan'))
        await asyncio.gather(*peer_remove_coros)
        print(c(f'{len(peer_remove_coros)} peer{"" if len(peer_remove_coros) < 2 else "s"} disconnected', 'cyan'))
    
    
    async def clear_conn_leds(self):
        if self.conn_led != None or self.data_led != None:
            if self.conn_led != None:
                self.conn_led.clear()
                self.conn_led = None
            if self.data_led != None:
                self.data_led.clear()
                self.data_led = None
        await asyncio.sleep(0.1) # wait a bit
    
    
    async def reset_conn_leds(self):
        if self.conn_led != None:
            self.conn_led.set_fast_pulse()
        if self.data_led != None:
            self.data_led.off()
    
    
    async def peer_signalling_stable_checker(self, peer:WRTCPeer) -> bool:
        timeout_sec = 10.0
        while peer.pc.signalingState != 'stable' and timeout_sec > 0.0:
            await asyncio.sleep(.1)
            timeout_sec -= .1
        if timeout_sec <= 0.0:
            self.get_logger().error(f'Timed out waiting for stable signalling, state={peer.pc.signalingState}, {peer}')
            return False
        return True
    
    async def peer_processing_state_checker(self, peer:WRTCPeer) -> bool:
        timeout_sec = 10.0
        while peer.processing_subscriptions and timeout_sec > 0.0:
            await asyncio.sleep(.1)
            timeout_sec -= .1
        if timeout_sec <= 0.0:
            self.get_logger().error(f'Timed out waiting for peer subs processing, state={peer.pc.signalingState}, {peer}')
            return False
        return True

    async def peer_ice_checker(self, peer:WRTCPeer) -> bool:
        timeout_sec = 20.0
        while peer.pc.iceGatheringState != 'complete' and timeout_sec > 0.0:
            await asyncio.sleep(.1)
            timeout_sec -= .1
        if timeout_sec <= 0.0:
            self.get_logger().error(f'Timed out waiting for ICE gathering, state={peer.pc.iceGatheringState}, {peer}')
            return False
        return True


    def on_msg_blink(self):
        if self.data_led != None:
            self.data_led.once() # blink when sending data to a peer


    # SUBSCRIBE data topic
    async def subscribe_data_topic(self, topic:str, qos:QoSProfile, peer:WRTCPeer=None, msg_callback=None) -> str:

        if not topic in self.introspection.discovered_topics.keys():
            return None

        msg_type:str = self.introspection.discovered_topics[topic]['msg_type']
        if IsImageType(msg_type):
            return None

        if not topic in self.topic_read_subscriptions.keys():
            self.topic_read_subscriptions[topic] = TopicReadSubscription(ctrl_node=self,
                                                                            worker_ctrl_queue=self.data_worker_ctrl_queue,
                                                                            topic=topic,
                                                                            protocol=msg_type,
                                                                            qos=qos,
                                                                            event_loop=asyncio.get_event_loop(),
                                                                            log_message_every_sec=self.log_message_every_sec,
                                                                            msg_blinker_cb = self.on_msg_blink
                                                                            )
            asyncio.get_event_loop().create_task(self.introspection.start())

        send_latest = False
        if peer:
            if not topic in peer.outbound_data_channels.keys():
                peer.wrtc_nextChannelId += 1
                is_reliable = qos.reliability == QoSReliabilityPolicy.RELIABLE
                dc:RTCDataChannel = peer.pc.createDataChannel(topic,
                                                                id=peer.wrtc_nextChannelId,
                                                                protocol=msg_type,
                                                                negotiated=True, # true = negotiated by the app, not webrtc layer
                                                                ordered=is_reliable,
                                                                maxRetransmits=None if is_reliable else 0)
                peer.outbound_data_channels[topic] = dc
                self.get_logger().debug(f'{peer} subscribed to {topic} (protocol={msg_type}, ch_id={dc.id}); reliable={is_reliable}')
                if is_reliable:
                    send_latest = True

        if not self.topic_read_subscriptions[topic].start(peer, msg_callback):
            self.get_logger().error(f'Topic {topic} failed to subscribee in on_read_subscriptions_change , peer={peer}, msg_callback={msg_callback}')
            return None

        if send_latest:
            asyncio.get_event_loop().create_task(self.topic_read_subscriptions[topic].report_latest_when_ready(peer, msg_callback))
        
        if peer != None:
            return peer.outbound_data_channels[topic].id
        else:
            return None

    # UNSUBSCRIBE data topic
    async def unsubscribe_data_topic(self, topic:str, peer:WRTCPeer=None, msg_callback=None):
        if peer and topic in peer.outbound_data_channels.keys():
            self.get_logger().debug(f'{peer} no longer subscribing to {topic}')
            id_closed_dc = peer.outbound_data_channels[topic].id
            peer.outbound_data_channels[topic].close()
            peer.outbound_data_channels.pop(topic)

        if topic in self.topic_read_subscriptions.keys():
            if await self.topic_read_subscriptions[topic].stop(peer, msg_callback):
                self.get_logger().debug(f'No longer reading {topic}')
                self.topic_read_subscriptions.pop(topic)
                asyncio.get_event_loop().create_task(self.introspection.start())


    # SUBSCRIBE image topic
    async def subscribe_image_topic(self, topic:str, qos:QoSProfile, peer:WRTCPeer) -> str:

        if not topic in self.introspection.discovered_topics.keys():
            return None

        msg_type:str = self.introspection.discovered_topics[topic]['msg_type']
        if not IsImageType(msg_type):
            return None

        if IsEncodedStreamType(msg_type):
            worker_type = 'video'
            ctrl_queue = self.video_worker_ctrl_queue
            out_queue = self.video_worker_out_queue
        else:
            worker_type = 'image'
            ctrl_queue = self.image_worker_ctrl_queue
            out_queue = self.image_worker_out_queue
        
        if not worker_type in self.image_topic_read_subscriptions.keys():
            self.image_topic_read_subscriptions[worker_type] = ImageTopicReadSubscription(ctrl_node=self,
                                                                                          worker_type=worker_type,
                                                                                          worker_ctrl_queue=ctrl_queue,
                                                                                          worker_out_queue=out_queue,
                                                                                          blocking_reads_executor=self.image_read_executor,
                                                                                          log_message_every_sec=self.log_message_every_sec,
                                                                                          msg_blinker_cb=self.on_msg_blink
                                                                                          )
            asyncio.get_event_loop().create_task(self.introspection.start())

        if not topic in peer.video_tracks.keys():
            track = CameraVideoStreamTrack()
            transceiver = peer.pc.addTransceiver(track, "sendonly")
            sender:RTCRtpSender = transceiver.sender
            sender.setDirect(True)
            
            # set transciever's preference to H264
            capabilities = RTCRtpSender.getCapabilities("video")
            preferences = list(filter(lambda x: x.mimeType == "video/H264", capabilities.codecs))
            transceiver.setCodecPreferences(preferences)

            # self.get_logger().info(f'sender._stream_id {sender._stream_id} >> {sender._track_id}')
            sender._stream_id = sender._track_id
            sender.name = topic
            sender.pc = peer.pc

            self.get_logger().info(f'Created video sender for {peer} {topic}, track_id=={sender._track_id}, capabilities: {str(sender.getCapabilities(kind="video"))}')

            @sender.track.on('ended')
            async def on_sender_track_ended():
                self.get_logger().warn(f'Sender video track ended for {peer} {topic}, track_id=={str(sender._track_id)}')

            peer.video_tracks[topic] = sender
            # await sender.track.set_frame(av.VideoFrame(width=640, height=480, format='rgb24'))

        if not self.image_topic_read_subscriptions[worker_type].start(id_peer=peer.id,
                                                                      msg_type=msg_type,
                                                                      topic=topic,
                                                                      qos=qos,
                                                                      peer_sender=peer.video_tracks[topic]):
            self.get_logger().error(f'Image topic {worker_type} failed to subscribe {topic} for {peer}')
            return { 'err': 2, 'msg': f'Image topic {worker_type} failed to subscribe {topic}'}

        return [ peer.video_tracks[topic]._track_id, msg_type ]


    # UNSUBSCRIBE image topic
    async def unsubscribe_image_topic(self, topic:str, peer:WRTCPeer):

        if topic in peer.video_tracks.keys():
            self.get_logger().debug(f'{peer} no longer subscribed to {topic}; removing track')
            await peer.video_tracks[topic].stop()
            peer.video_tracks.pop(topic)

        msg_type:str = self.introspection.discovered_topics[topic]['msg_type']
        worker_type = 'video' if IsEncodedStreamType(msg_type) else 'image'
        
        if worker_type in self.image_topic_read_subscriptions.keys():
            if self.image_topic_read_subscriptions[worker_type].stop(peer.id, topic): # truw if subscriber empty
                self.get_logger().debug(f'Removing {worker_type} worker')
                self.image_topic_read_subscriptions.pop(worker_type)
                asyncio.get_event_loop().create_task(self.introspection.start())


    # OPEN WRITE data channel
    async def open_write_channel(self, topic:str, msg_type:str, peer:WRTCPeer) -> str:
        if topic != '_heartbeat':
            if not topic in self.topic_write_publishers:
                self.topic_write_publishers[topic] = TopicWritePublisher(node=self,
                                                                        topic=topic,
                                                                        protocol=msg_type,
                                                                        log_message_every_sec=self.log_message_every_sec)
                asyncio.get_event_loop().create_task(self.introspection.start()) # created publisher => inrospect & update

            if not self.topic_write_publishers[topic].start(peer.id):
                self.get_logger().error(f'Topic {topic} failed to start publisher in on_write_subscription_change, {peer}')
                return None

        if not topic in peer.inbound_data_channels.keys():
            dc = self.make_publisher_dc(peer, topic, msg_type)
            if dc is not None:
                peer.inbound_data_channels[topic] = dc
                self.get_logger().debug(f'{peer} publishing into {topic} (protocol={msg_type}, ch_id={dc.id})')

        if not topic in peer.inbound_data_channels.keys():
            self.get_logger().error(f'Topic {topic} failed to open write subscription for {peer}')
            return None

        return peer.inbound_data_channels[topic].id
        # res_subscribed.append([topic, peer.inbound_data_channels[topic].id, protocol])


    # CLOSE WRITE data channel
    async def close_write_channel(self, topic:str, peer:WRTCPeer):
        if topic in peer.inbound_data_channels.keys():
            self.get_logger().debug(f'{peer} no longer publishing into {topic}')
            id_closed_dc = peer.inbound_data_channels[topic].id
            peer.inbound_data_channels[topic].close()
            peer.inbound_data_channels.pop(topic)

        if topic in self.topic_write_publishers:
            if self.topic_write_publishers[topic].stop(peer.id):
                self.get_logger().debug(f'No longer publishing into {topic}')
                self.topic_write_publishers.pop(topic)
                asyncio.get_event_loop().create_task(self.introspection.start()) # removed publisher => inrospect & update


    ##
    # Topic READ subscriptions & message routing
    ##
    async def on_read_subscriptions_change(self, peer:WRTCPeer, data:dict) -> {}:
        pass
       

    def make_publisher_dc(self, peer, topic, protocol) -> RTCDataChannel:
        peer.wrtc_nextChannelId += 1
        is_heartbeat = (topic == '_heartbeat')
        dc = peer.pc.createDataChannel(topic,
                                        id=peer.wrtc_nextChannelId,
                                        protocol=protocol,
                                        negotiated=True, # true = negotiated by the app, not webrtc layer
                                        ordered=False,
                                        maxRetransmits=None)
        @dc.on('message')
        def on_inbound_channel_message(msg):
            if is_heartbeat:
                if self.get_parameter('log_heartbeat').get_parameter_value().bool_value:
                    print(f'🫀 Got heartbeat from '+peer.id)
                peer.last_heartbeat = time.time()
            else:
                self.topic_write_publishers[topic].publish(peer.id, msg)
        return dc

    ##
    # Topic WRITE subscriptions & message routing
    ###
    async def on_write_subscription_change(self, peer:WRTCPeer, data:dict) -> {}:
        pass
        

    ##
    # ROS Service call handling
    ##
    async def on_service_call(self, id_peer:str, data:dict, event_loop:any):
        service = data['service']
        if not service:
            self.get_logger().error(f'No service name provided by peer={id_peer}, ignoring call')
            return { 'err': 2, 'msg': f'No service name provided' }
        
        payload = None
        if 'msg' in data.keys():
            payload = data['msg']

        self.get_logger().debug(f"Peer {id_peer} calling service {service} with args: {str(payload)}")

        if not service in self.introspection.discovered_services.keys():
            self.get_logger().error(f'Service {service} not discovered (yet?) for peer={id_peer}')
            return { 'err': 2, 'msg': f'Service {service} not discovered (yet?)' }

        message_class = None
        msg_type = self.introspection.discovered_services[service]["msg_type"]
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

        try:
            req = message_converter.convert_dictionary_to_ros_message(
                message_class.Request,
                payload
                )
        except Exception as e:
            self.get_logger().error(f'Error making service message for {service}: {e}; payload={str(payload)}')
            return { 'err': 2, 'msg': f'{e}' }
        
        future = self.service_clients[service].call_async(req)
        # ftrs = set()
        # rclpy.spin_until_future_complete(self, self.future)
        async def srv_finished_checker():
            timeout_sec = 10.0
            while not future.done() and not self.shutting_down and timeout_sec > 0.0:
                try:
                    await event_loop.run_in_executor(None, lambda: self.rcl_executor.spin_once(timeout_sec=0.1)) # gotta spin to hear back
                    # await fut
                except Exception as e:
                    if (str(e) != 'cannot use Destroyable because destruction was requeste'):
                        print(f'Exception while spinning node for service {service}: {e}')
                await asyncio.sleep(.1)
                timeout_sec -= .1
            is_timeout = timeout_sec <= 0.0
            self.get_logger().warn(f"Service {service} call finished with result: {str(future.result())}{(' TIMEOUT' if is_timeout else '')}")
            return is_timeout
        is_timeout = await srv_finished_checker()

        if is_timeout:
            return { 'err': 2, 'msg': f'Service execution timeout' }
        
        reply = message_converter.convert_ros_message_to_dictionary(future.result())
        self.get_logger().debug(f'Returning service {service} reply: {str(reply)}')
        return reply
    
    def shutdown(self):
        # Cleanup code goes here
        self.get_logger().info('Performing cleanup before shutdown')
        print('Performing cleanup before shutdown')
        # Example: close file handles, stop motors, etc.

    async def shutdown_cleanup(self):

        try:
            await self.clear_conn_leds()
             
            await self.introspection.stop(report=False)
            
            # close peer connections     
            await self.remove_all_peers()

            await self.sio.disconnect()
            if self.spin_task != None and not self.spin_task.done():
                self.spin_task.cancel()

            if self.sio_wait_task != None and not self.sio_wait_task.done():
                self.sio_wait_task.cancel()

            if self.sio_reconnect_wait_task != None and not self.sio_reconnect_wait_task.done():
                self.sio_reconnect_wait_task.cancel()
            
            print('Shutdown cleanup complete')
        except Exception as e:
            print(c(f'Exception in shutdown_cleanup: {e}', 'red'))


async def main_async(rcl_context, rcl_executor):

    workers_enabled = mp.Value('b', 1, lock=False) # controls workers
    
    video_worker_ctrl_queue = mp.Queue()
    video_worker_out_queue = mp.Queue(20)
    video_topic_read_worker = mp.Process(target=TopicProcessorWorker,
                                      args=(workers_enabled,
                                            'video',
                                            video_worker_ctrl_queue,
                                            video_worker_out_queue
                                            ))
    video_topic_read_worker.start()
    
    data_worker_ctrl_queue = mp.Queue()
    data_topic_read_worker = mp.Process(target=TopicProcessorWorker,
                                      args=(workers_enabled,
                                            'data',
                                            data_worker_ctrl_queue,
                                            None))
    data_topic_read_worker.start()
    
    image_worker_ctrl_queue = mp.Queue()
    image_worker_out_queue = mp.Queue(20)
    image_topic_read_worker = mp.Process(target=TopicProcessorWorker,
                                      args=(workers_enabled,
                                            'img',
                                            image_worker_ctrl_queue,
                                            image_worker_out_queue
                                            ))
    image_topic_read_worker.start()
    
    bridge_node = BridgeController(context=rcl_context, executor=rcl_executor)
    rcl_executor.add_node(bridge_node)

    try:
        bridge_node.start(video_worker_ctrl_queue=video_worker_ctrl_queue,
                          video_worker_out_queue=video_worker_out_queue,
                          image_worker_ctrl_queue=image_worker_ctrl_queue,
                          image_worker_out_queue=image_worker_out_queue,
                          data_worker_ctrl_queue=data_worker_ctrl_queue)
        
        sio_task = asyncio.get_event_loop().create_task(bridge_node.spin_sio_client(), name="sio_task")
        asyncio.get_event_loop().create_task(bridge_node.introspection.start(), name="initial_introspection_task")
        
        await asyncio.wait([ sio_task ], return_when=asyncio.ALL_COMPLETED)

    except (asyncio.CancelledError, KeyboardInterrupt):
        print(c('Shutting down main_async', 'red'))
        pass
    except Exception as e:
        print(c('Exception in main_async()', 'red'))
        traceback.print_exc(e)

    print(c('SHUTTING DOWN', 'cyan'))

    bridge_node.shutting_down = True

    workers_enabled.value = 0 # stops worker threads

    video_topic_read_worker.terminate()
    video_topic_read_worker.join()
    
    image_topic_read_worker.terminate()    
    image_topic_read_worker.join()
    
    data_topic_read_worker.terminate()
    data_topic_read_worker.join()
    
    image_worker_ctrl_queue.close()
    data_worker_ctrl_queue.close()
    video_worker_ctrl_queue.close()
    
    if sio_task != None and not sio_task.done():
        sio_task.cancel()

    await bridge_node.shutdown_cleanup()

    try:
        print('Destroying Bridge node')
        bridge_node.destroy_node()
    except Exception as e:
        print(c(f'Error while shutting down rcl: {e}', 'red'))


def first_run_checks():
    first_run_file = '/.phntm_first_run'
    force_first_run_checks = 'FORCE_FIRST_RUN_CHECKS' in os.environ.keys()
    force_first_run_ignore_file = '/.phntm_ignore_force_first_run'
    if force_first_run_checks and os.path.exists(force_first_run_ignore_file): # just restarted after forced check, ignore to allow normal start
        os.remove(force_first_run_ignore_file)
        force_first_run_checks = False
    
    if force_first_run_checks or not os.path.exists(first_run_file):
        
        print(c('First run, checking extra packages', 'magenta'))
        
        # load node name from config before we can set node name
        config_path = '/ros2_ws/phntm_bridge_params.yaml'
        try:
            with open(config_path, 'r') as file:
                config = yaml.safe_load(file)
                extra_packages = config["/**"]["ros__parameters"].get('extra_packages', [])
                for extra_pkg in extra_packages:
                    print(c(f'Checking package: {extra_pkg}', 'yellow'))
                    if os.path.isdir(extra_pkg):
                        print(f'  => directory, checking package.xml')
                        pkg_xml_path = os.path.join(extra_pkg, 'package.xml')
                        try:
                            xml_tree = XmlET.parse(pkg_xml_path)
                            print(f'  {pkg_xml_path} parsed')
                            xml_root = xml_tree.getroot()
                            if xml_root == None:
                                print(c(f'  XML root not found, skipping', 'red'))
                                continue
                            build_type = xml_root.find('export/build_type')
                            if build_type == None:
                                print(c(f'  export/build_type not found, skipping', 'red'))
                                continue
                            pkg_name = xml_root.find('name')
                            if pkg_name == None:
                                print(c(f'  export/build_type not found, skipping', 'red'))
                                continue
                            print(f'  name: {pkg_name.text}')
                            print(f'  build_type: {build_type.text}')
                            try:
                                print(f'  installing deps...')
                                process = subprocess.Popen(['/usr/bin/rosdep', 'install', '-i', '--from-path', extra_pkg, '--rosdistro', os.environ["ROS_DISTRO"], '-y'], cwd='/ros2_ws/')
                                process.wait()
                                print(f'  building...')
                                process = subprocess.Popen(['/usr/bin/colcon', 'build', '--symlink-install', '--packages-select', pkg_name.text], cwd='/ros2_ws/')
                                process.wait()
                            except Exception as e:
                                print(c(f'  Error building {e}', 'red'))
                                pass
                            print(f'  Done.')
                        except FileNotFoundError:
                            print(c(f'  {pkg_xml_path} not found, skipping', 'red'))
                            pass
                    else:
                        pkg_name = 'ros-'+os.environ["ROS_DISTRO"]+'-'+extra_pkg.replace('_', '-')
                        print(f'  => package, installing {pkg_name}...')
                        process = subprocess.Popen(['/usr/bin/apt-get', 'install', '-y', pkg_name])
                        process.wait()
                # node_name = f'{node_name}_{self.hostname}' if self.hostname else node_name
        except FileNotFoundError:
            print(c(f'{config_path} not found, ignoring', 'magenta'))
            pass
        
        open(first_run_file, 'w').close()
        if force_first_run_checks:
            open(force_first_run_ignore_file, 'w').close() # ignore force after restart
        
        print(c('Restarting Bridge...', 'magenta'))
        exit()

class MyAsyncioPolicy(asyncio.DefaultEventLoopPolicy):
    def new_event_loop(self):
        selector = selectors.SelectSelector()
        return asyncio.SelectorEventLoop(selector)

def main(): # ros2 calls this, so init here
    
    first_run_checks() # installs custom packages
    
    asyncio.set_event_loop_policy(MyAsyncioPolicy())
    
    rcl_context = rclpy.context.Context()
    rcl_context.init()
    rcl_executor = SingleThreadedExecutor(context=rcl_context)
    
    try:
        asyncio.run(main_async(rcl_context=rcl_context, rcl_executor=rcl_executor))
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    
    print('AsyncIO loop stopped')
    try:
        rcl_context.shutdown()
    except:
        pass

if __name__ == '__main__':
    main()