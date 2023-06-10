#!/usr/bin/env python3

import rclpy
from rclpy.node import Node, Parameter, Subscription, QoSProfile, Publisher
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy
from rclpy.duration import Duration, Infinite

from .inc.status_led import StatusLED
from .inc.ros_video_streaming import ROSVideoStreamTrack
from rcl_interfaces.msg import ParameterDescriptor
import signal
import time

from sensor_msgs.msg import Image
from sensor_msgs.msg import BatteryState


# from rclpy.subscription import TypeVar
from ros2topic.api import get_msg_class, get_message

import asyncio
from asyncio import Condition

# import engineio
import threading
import socketio
# from websockets.sync.client import connect

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer, MediaRelay
from aiortc.rtcrtpsender import RTCRtpSender

import ssl
from aiortc.contrib.media import MediaPlayer, MediaRelay
from aiortc.rtcrtpsender import RTCRtpSender
import os
import platform

ROOT = os.path.dirname(__file__)

class BridgeController(Node):
    def __init__(self):
        super().__init__('webrtc_bridge')

        self.is_connected_ = False
        self.shutting_down_ = False

        self.event_loop = asyncio.get_event_loop()

        # ID ROBOT
        self.declare_parameter('id_robot', '')
        self.declare_parameter('name', 'Unnamed Robot')
        self.declare_parameter('key', '')
        self.id_robot = self.get_parameter('id_robot').get_parameter_value().string_value
        self.robot_name = self.get_parameter('name').get_parameter_value().string_value
        self.auth_key = self.get_parameter('key').get_parameter_value().string_value
        if (self.id_robot == None or self.id_robot == ''):
            self.get_logger().error(f'Param id_robot not provided!')
            exit(1)
        if (self.auth_key == None or self.auth_key == ''):
            self.get_logger().error(f'Param key not provided!')
            exit(1)

        # SOCKET.IO
        self.declare_parameter('sio_address', 'https://api.phntm.io')
        self.declare_parameter('sio_port', 1337)
        self.declare_parameter('sio_path', '/robot/socket.io')
        self.declare_parameter('sio_connection_retry_sec', 2.0)
        self.declare_parameter('sio_ssl_verify', True)

        self.declare_parameter('log_message_every_sec', 10.0)
        self.log_message_every_sec = self.get_parameter('log_message_every_sec').get_parameter_value().double_value

        self.declare_parameter('topic_overrides', [ '' ])
        self.topic_overrides = self.get_parameter('topic_overrides').get_parameter_value().string_array_value
        for topic_override in self.topic_overrides:
            if topic_override == '':
                continue
            self.declare_parameter(f'{topic_override}.name', '')
            self.declare_parameter(f'{topic_override}.durability', 0)
            self.declare_parameter(f'{topic_override}.raw', True)
            self.declare_parameter(f'{topic_override}.reliability', 0)


        print('topic_overrides', self.topic_overrides)

        self.sio_address = self.get_parameter('sio_address').get_parameter_value().string_value
        self.sio_port = self.get_parameter('sio_port').get_parameter_value().integer_value
        self.sio_path = self.get_parameter('sio_path').get_parameter_value().string_value
        self.sio_ssl_verify = self.get_parameter('sio_ssl_verify').get_parameter_value().bool_value
        self.sio_connection_retry_sec = self.get_parameter('sio_connection_retry_sec').get_parameter_value().double_value
        if (self.sio_address == None or self.sio_address == ''): self.get_logger().error(f'Param sio_address not provided!')
        if (self.sio_port == None): self.get_logger().error(f'Param sio_port not provided!')

        # Comm LED
        self.declare_parameter('conn_led_topic', '' )
        self.conn_led_topic = self.get_parameter('conn_led_topic').get_parameter_value().string_value
        self.conn_led = None
        if (self.conn_led_topic != None and self.conn_led_topic != ''):
            self.get_logger().info(f'CONN Led uses {self.conn_led_topic}')
            self.conn_led = StatusLED('conn', StatusLED.Mode.OFF, self.conn_led_topic, QoSProfile(depth=1, reliability=QoSReliabilityPolicy.BEST_EFFORT))
            self.conn_led.set_fast_pulse()
            #self.led_spinner = self.create_timer(0.1, lambda: rclpy.spin_once(self.status_led))

        # Net LED
        self.declare_parameter('data_led_topic', '' )
        self.data_led_topic = self.get_parameter('data_led_topic').get_parameter_value().string_value
        self.data_led = None
        if (self.data_led_topic != None and self.data_led_topic != ''):
            self.get_logger().info(f'DATA Led uses {self.data_led_topic}')
            self.data_led = StatusLED('data', StatusLED.Mode.OFF, self.data_led_topic, QoSProfile(depth=1, reliability=QoSReliabilityPolicy.BEST_EFFORT))
            #self.data_led.off()

        # TOPIC DICOVERY
        self.declare_parameter('discovery_period_sec', 5.0)
        self.declare_parameter('topic_whitelist', [ '.*' ])
        self.declare_parameter('topic_blacklist', [ '/led/', '/_', '/rosout' ] )
        self.declare_parameter('param_whitelist', [ '.*' ])
        self.declare_parameter('param_blacklist', [ '' ])

        self.discovered_topics_:dict[str: dict['msg_types':list[str]]] = {}
        self.topic_read_subscriptions_:dict[str: tuple[
            Subscription, # reader
            int, # num received msgs
            any, # last msg
            float, # last msg received time (s)
            list[str], #subscribed peers
            float #last time logged (s)
        ] ] = {}

        self.topic_publishers_:dict[str: tuple[
            Publisher,
            int, # num written msgs
            any, # last msg
            float, # last msg received time(s)
            list[str], #publishing peers
            float #last time logged (s)
        ] ] = {}

        self.wrtc_nextChannelId = 1
        self.wrtc_peer_pcs:dict[str,RTCPeerConnection] = dict() # id_peer => pc
        self.wrtc_peer_read_channels:dict[str,dict[str,RTCDataChannel]] = dict() # id_peer => [ topic => read webrtc channel ]
        self.wrtc_peer_write_channels:dict[str,dict[str,RTCDataChannel]] = dict() # id_peer => [ topic => write webrtc channel ]

        self.spin_thread: threading.Thread = None
        self.spin_task: asyncio.Future[any] = None
        self.sio_wait_task: asyncio.Future[any] = None
        self.sio_reconnect_wait_task: asyncio.Future[any] = None

        # self.wrtc_relay:MediaRelay = None
        # self.wrtc_webcam:MediaPlayer = None
        # self.data_sender:RTCDataChannel = None

        self.get_logger().info(f'Phntm WebRTC Bridge started, idRobot={self.id_robot}')

    def start_discovery(self):
        # run now
        asyncio.get_event_loop().create_task(self.discover_topics())
        #repeat with timer
        self.discovery_timer_ = self.create_timer(self.get_parameter('discovery_period_sec').get_parameter_value().double_value, self.discover_topics)
        self.get_logger().info("start_discovery finished")

    async def report_topics(self):
        if not self.is_connected_:
            return

        data = []

        for topic in self.discovered_topics_.keys():
            topic_data = [
                topic,
                topic in self.topic_read_subscriptions_.keys(), # subsribed here
                # msg types follow
            ]
            for msg_type in self.discovered_topics_[topic]['msg_types']:
                topic_data.append(msg_type)
            data.append(topic_data)

        await self.sio.emit(
            event='topics',
            data=data,
            callback=None
            )

    async def report_data(self, topic:str, payload:any, log:bool, total_sent:int):

        for id_peer in self.wrtc_peer_read_channels.keys():
            if topic in self.wrtc_peer_read_channels[id_peer].keys():
                dc = self.wrtc_peer_read_channels[id_peer][topic]
                self.wrtc_peer_read_channels[id_peer][topic]
                if dc.readyState == 'open':
                    if type(payload) is bytes:
                        if log:
                            self.get_logger().info(f'△ Sending {len(payload)}B into {topic} for id_peer={id_peer}, total sent: {total_sent}')
                        dc.send(payload) #raw
                    else:
                        if (log):
                            self.get_logger().info(f'△ Sending {type(payload)} into {topic} for id_peer={id_peer}, total sent: {total_sent}')
                        dc.send(str(payload)) #raw

                    if self.data_led != None:
                        self.data_led.once()

    async def report_latest_when_ready(self, id_peer:str, topic:str):

        while True:

            if not id_peer in self.wrtc_peer_read_channels.keys():
                return
            if not topic in self.wrtc_peer_read_channels[id_peer].keys():
                return
            if not topic in self.topic_read_subscriptions_.keys():
                return #not subscribed yet

            payload = self.topic_read_subscriptions_[topic][2] # save latest

            if payload == None:
                return #nothing received yet

            dc = self.wrtc_peer_read_channels[id_peer][topic]
            if dc.readyState == 'open':
                if type(payload) is bytes:
                    self.get_logger().info(f'Sending latest {len(payload)}B into {topic} id_peer={id_peer}')
                    dc.send(payload) #raw
                else:
                    self.get_logger().info(f'Sending latest {type(payload)} into {topic} id_peer={id_peer}')
                    dc.send(str(payload)) #raw
                return
            else:
                await asyncio.sleep(1) #wait until dc opens


    async def start_sio_client(self, addr:str, port:int, path:str):

        sio = socketio.AsyncClient(handle_sigint=False,
                                   logger=True,
                                   ssl_verify=self.sio_ssl_verify
                                   )

        @sio.on('connect')
        async def on_connect():
            self.get_logger().warn('Socket.io connection established, auth successful')

            self.is_connected_ = True

            if self.conn_led != None:
                self.conn_led.on()

            await asyncio.get_event_loop().create_task(self.report_topics())

        @sio.on('offer')
        async def on_offer(data):
             return await self.on_wrtc_offer(data)

        # subscribe and unsubscribe data channels
        # bcs the negotionation doesn't seem to be implemented well at the moment
        # it's be nice to do thisvia webrtc tho
        @sio.on('subscription:read')
        async def on_read_subscription(data:dict):

            # self.get_logger().info('on_read_subscription: ' + str(data))

            id_peer:str = None
            if 'id_app' in data:
                id_peer = data['id_app']
            if 'id_instance' in data:
                id_peer = data['id_instance']
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }

            if not id_peer in self.wrtc_peer_pcs:
                 return { 'err': 2, 'msg': 'Peer not connected' }
            pc = self.wrtc_peer_pcs[id_peer]

            if not 'topics' in data:
                return { 'err': 2, 'msg': 'No topics specified' }

            res_subscribed:list[tuple[str,int]] = list() # [ topic, id_ch ]
            res_unsubscribed:list[tuple[str,int]] = list()

            for topic_data in data['topics']: # : [ topic, subscribe, ...]
                topic:str = topic_data[0]
                subscribe:bool = int(topic_data[1]) > 0

                if subscribe:
                    if not topic in self.discovered_topics_.keys():
                        return { 'err': 2, 'msg': f'Topic {topic} not discovered yet'}

                    if not id_peer in self.wrtc_peer_read_channels:
                        self.wrtc_peer_read_channels[id_peer] = dict()

                    if not topic in self.wrtc_peer_read_channels[id_peer]:
                        protocol:str = ', '.join(self.discovered_topics_[topic]['msg_types'])

                        self.wrtc_nextChannelId += 1
                        self.get_logger().info(f'Peer {id_peer} subscribing to {topic}/R (protocol={protocol}, ch_id={self.wrtc_nextChannelId})')
                        dc = pc.createDataChannel(topic, id=self.wrtc_nextChannelId, protocol=protocol, negotiated=True) # negotiated doesn't work tho

                        @dc.on('message')
                        async def on_channel_message(msg):
                            self.get_logger().info(f' ⇣ {topic}/R got message: '+str(msg))

                        self.wrtc_peer_read_channels[id_peer][topic] = dc
                        res_subscribed.append([topic, self.wrtc_nextChannelId])

                        if not self.subscribe_topic(topic, id_peer):
                            return { 'err': 2, 'msg': f'Topic {topic} failed to subscribe'}

                        await self.report_latest_when_ready(id_peer, topic)

                else: # unsubscribe
                    if id_peer in self.wrtc_peer_read_channels and topic in self.wrtc_peer_read_channels[id_peer]:
                        self.get_logger().info(f'Peer {id_peer} unsubscribed from {topic}/R')
                        id_closed_dc = self.wrtc_peer_read_channels[id_peer][topic].id
                        self.wrtc_peer_read_channels[id_peer][topic].close()
                        self.wrtc_peer_read_channels[id_peer].pop(topic)
                        res_unsubscribed.append([ topic, id_closed_dc ])

                    self.unsubscribe_topic(topic, id_peer)

            return { 'success': 1, 'subscribed': res_subscribed, 'unsubscribed': res_unsubscribed}

        # WRITE SUBS
        @sio.on('subscription:write')
        async def on_write_subscription(data:dict):

            self.get_logger().info('on_write_subscription: ' + str(data))

            id_peer:str = None
            if 'id_app' in data:
                id_peer = data['id_app']
            if 'id_instance' in data:
                id_peer = data['id_instance']
            if id_peer == None:
                return { 'err': 2, 'msg': 'No valid peer id provided' }

            if not id_peer in self.wrtc_peer_pcs:
                 return { 'err': 2, 'msg': 'Peer not connected' }
            pc = self.wrtc_peer_pcs[id_peer]

            if not 'topics' in data:
                return { 'err': 2, 'msg': 'No topics specified' }

            res_subscribed:list[tuple[str,int]] = list() # [ topic, id_ch ]
            res_unsubscribed:list[tuple[str,int]] = list()

            for topic_data in data['topics']: # : [ topic, subscribe, ...]
                topic:str = topic_data[0]
                subscribe:bool = int(topic_data[1]) > 0
                protocol:str = topic_data[2]

                if protocol == None:
                    return { 'err': 2, 'msg': f'Protocol not specified for {topic}' }

                if subscribe:
                    # if not topic in self.discovered_topics_.keys():
                    #    return { 'err': 2, 'msg': f'Topic {topic} not discovered yet'}

                    if not id_peer in self.wrtc_peer_write_channels:
                        self.wrtc_peer_write_channels[id_peer] = dict()

                    if not topic in self.wrtc_peer_write_channels[id_peer]:


                        self.wrtc_nextChannelId += 1
                        self.get_logger().info(f'Peer {id_peer} subscribing to {topic}/W (protocol={protocol}, ch_id={self.wrtc_nextChannelId})')
                        dc = pc.createDataChannel(topic, id=self.wrtc_nextChannelId, protocol=protocol, negotiated=True) # negotiated doesn't work tho

                        self.wrtc_peer_write_channels[id_peer][topic] = dc
                        res_subscribed.append([topic, self.wrtc_nextChannelId, protocol])

                        if not self.start_topic_publisher(topic, id_peer, protocol):
                            return { 'err': 2, 'msg': f'Topic {topic} failed to create publisher'}

                        @dc.on('message')
                        async def on_channel_message(msg):

                            self.topic_publishers_[topic][1] += 1
                            self.topic_publishers_[topic][2] = msg
                            self.topic_publishers_[topic][3] = time.time()

                            if time.time()-self.topic_publishers_[topic][5] > self.log_message_every_sec:
                                self.topic_publishers_[topic][5] = time.time() #logged now
                                if type(msg) is bytes:
                                    self.get_logger().info(f'▼ {topic}/W got message: {len(msg)}B from id_peer={id_peer}, total rcvd: {self.topic_publishers_[topic][1]}')
                                else:
                                    self.get_logger().info(f'▼ {topic}/W got message: {len(msg)}, from id_peer={id_peer}, total rcvd: {self.topic_publishers_[topic][1]}')

                            self.topic_publishers_[topic][0].publish(msg);

                else: # unsubscribe
                    if id_peer in self.wrtc_peer_write_channels and topic in self.wrtc_peer_write_channels[id_peer]:
                        self.get_logger().info(f'Peer {id_peer} unsubscribed from {topic}/W')
                        id_closed_dc = self.wrtc_peer_write_channels[id_peer][topic].id
                        self.wrtc_peer_write_channels[id_peer][topic].close()
                        self.wrtc_peer_write_channels[id_peer].pop(topic)
                        res_unsubscribed.append([ topic, id_closed_dc ])

                    self.stop_topic_publisher(topic, id_peer)

            return { 'success': 1, 'subscribed': res_subscribed, 'unsubscribed': res_unsubscribed}

        @sio.event
        async def connect_error(data):
            self.get_logger().error('Socket.io connection failed: ' + str(data))

        @sio.on('message')
        async def on_message(data):
            self.get_logger().info('Socket.io message received with ' + str(data))
            self.sio.send({'response': 'my response'})

        @sio.on('disconnect')
        async def on_disconnect():
            self.get_logger().warn('Socket.io disconnected from server')
            if self.conn_led != None:
                self.is_connected_ = False
                if not self.shutting_down_:
                    self.conn_led.set_fast_pulse()
                else:
                    self.conn_led.off()

        @sio.on('*')
        async def catch_all(event, data):
            self.get_logger().warn('Socket.io event ' + str(event) + ' data: ' + str(data))

        # @self.eio.on('error')
        # async def on_error():
        #     print('socket.io error')

        self.sio = sio

        while not self.shutting_down_:
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
                self.get_logger().info(f'Socket.io connection error, retrying in {self.sio_connection_retry_sec}s...')
                self.sio_reconnect_wait_task = asyncio.get_event_loop().create_task(asyncio.sleep(self.sio_connection_retry_sec))
                await self.sio_reconnect_wait_task
                self.sio_reconnect_wait_task = None
            except asyncio.CancelledError:
                self.get_logger().info('CancelledError')
                return


    def subscribe_topic(self, topic:str, id_peer:str) -> bool:

        if topic in self.topic_read_subscriptions_:
            if not id_peer in self.topic_read_subscriptions_[topic][4]:
                self.topic_read_subscriptions_[topic][4].append(id_peer)
            return True # we cool here

        topic_info = self.discovered_topics_[topic]
        if topic_info == None:
            return False

        msg_type:str = topic_info['msg_types'][0]
        message_class = None
        try:
            message_class = get_message(msg_type)
        except:
            pass

        if message_class == None:
            self.get_logger().error(f'NOT subscribing to topic {topic}, msg class {msg_type} not loaded (all={", ".join(topic_info["msg_types"])} )')
            return False

        #is_text = msg_type == 'std_msgs/msg/String'
        raw = self.get_parameter_or(f'{topic}.raw', Parameter(name='', value=True)).get_parameter_value().bool_value
        reliability = self.get_parameter_or(f'{topic}.reliability', Parameter(name='', value=QoSReliabilityPolicy.BEST_EFFORT)).get_parameter_value().integer_value
        durability = self.get_parameter_or(f'{topic}.durability', Parameter(name='', value=DurabilityPolicy.SYSTEM_DEFAULT)).get_parameter_value().integer_value

        qosProfile = QoSProfile(history=QoSHistoryPolicy.KEEP_LAST, \
                                depth=1, \
                                reliability=reliability, \
                                durability=durability, \
                                lifespan=Infinite \
                                )

        self.get_logger().warn(f'Subscribing to topic {topic} {msg_type} raw={raw}')

        sub = self.create_subscription(
            msg_type=message_class,
            topic=topic,
            callback=lambda msg: self.topic_subscriber_callback(topic, msg),
            qos_profile=qosProfile,
            raw=raw
        )
        if sub == None:
            self.get_logger().error(f'Failed subscribing to topic {topic}, msg class={msg_type} (all={", ".join(topic_info["msg_types"])} )')
            return False

        self.topic_read_subscriptions_[topic] = [
            sub,
            0, # num received msgs
            None, # last msg
            -1.0, #l ast timestamp
            [ id_peer ], # subscribers
            -1.0 # last time logged
        ]

        return True


    def unsubscribe_topic(self, topic:str, id_peer:str):
        if not topic in self.topic_read_subscriptions_.keys():
            self.get_logger().error(f'{topic} not found in self.topic_read_subscriptions_ ({str(self.topic_read_subscriptions_)})')
            return

        if id_peer in self.topic_read_subscriptions_[topic][4]:
            self.topic_read_subscriptions_[topic][4].remove(id_peer)

        self.get_logger().info(f'{topic} remaining subs: {len(self.topic_read_subscriptions_[topic][4])} {str(self.topic_read_subscriptions_[topic][4])}')

        if len(self.topic_read_subscriptions_[topic][4]) == 0: #no subscribers => clear
            self.get_logger().warn(f'Unsubscribing from topic {topic} (no subscribers)')
            self.destroy_subscription(self.topic_read_subscriptions_[topic][0])
            del self.topic_read_subscriptions_[topic]


    def topic_subscriber_callback(self, topic, msg):
        self.topic_read_subscriptions_[topic][1] += 1 # num recieved
        self.topic_read_subscriptions_[topic][2] = msg #latest val
        self.topic_read_subscriptions_[topic][3] = time.time() #latest time

        # if self.is_connected_ and topic != self.data_led_topic and self.data_led != None:
        #   self.data_led.once()

        log_msg = False
        if self.topic_read_subscriptions_[topic][1] == 1: # first data in
            self.get_logger().info(f'Receiving {type(msg).__name__} from {topic}')

        if self.topic_read_subscriptions_[topic][5] < 0 or time.time()-self.topic_read_subscriptions_[topic][5] > self.log_message_every_sec:
            log_msg = True
            self.topic_read_subscriptions_[topic][5] = time.time() #last logged now

        self.event_loop.create_task(self.report_data(topic, msg, log=log_msg, total_sent=self.topic_read_subscriptions_[topic][1]))

            # vif topic == '/joy' and self.conn_led != None:
            #    self.is_connected_ = True


            # if topic == '/cmd_vel' and self.conn_led != None:
            #    self.conn_led.set_disconnected()

    def start_topic_publisher(self, topic:str, id_peer:str, protocol:str) -> bool:

        message_class = None
        try:
            message_class = get_message(protocol)
        except:
            pass

        if message_class == None:
            self.get_logger().error(f'NOT creating publisher for topic {topic}, msg class {protocol} not loaded')
            return False

        qos = QoSProfile(history=QoSHistoryPolicy.KEEP_LAST, \
                         depth=1, \
                         reliability=QoSReliabilityPolicy.RELIABLE \
                         )

        pub = self.create_publisher(message_class, topic, qos)
        if pub == None:
            self.get_logger().error(f'Failed creating publisher for topic {topic}, msg class={protocol}')
            return False

        self.topic_publishers_[topic] = [
            pub,
            0, # num sent msgs
            None, # last msg
            -1.0, # last timestamp
            [ id_peer ], # publisher peers
            -1.0 # last logged never
        ]

        return True

    def stop_topic_publisher(self, topic:str, id_peer:str):
        pass


    async def discover_topics(self):
        topics_changed = False
        new_topics = self.get_topic_names_and_types()
        # self.get_logger().info(f'Seeing {len(discovered_topics)} topics...')

        for topic_info in new_topics:
            topic = topic_info[0]

            # TODO: blacklist topics with old filters

            if not topic in self.discovered_topics_:
                self.get_logger().info(f'Discovered topic {topic}')
                self.discovered_topics_[topic] = { 'msg_types': topic_info[1] }
                topics_changed = True

                # if not key_in_list(topic[0], self.subscribed_topics_) \
                #     and matches_param_filters(topic[0], self.get_parameter('topic_whitelist')) \
                #     and not matches_param_filters(topic[0], self.get_parameter('topic_blacklist')):
                #     self.topic_subscribe(topic)
                #     report_change = True
                # else:
                #     self.get_logger().debug(f'Ignoring topic {topic[0]} (filters)')

        if topics_changed:
            self.event_loop.create_task(self.report_topics())


    async def shutdown_cleanup(self):
        self.get_logger().warn('SHUTTING DOWN')
        self.shutting_down_ = True

        # close peer connections
        coros = [pc.close() for pc in self.wrtc_peer_pcs.values()]
        await asyncio.gather(*coros)
        self.wrtc_peer_pcs.clear()

        await self.sio.disconnect()
        self.spin_task.cancel()

        if self.sio_wait_task != None:
            self.sio_wait_task.cancel()
            await self.sio_wait_task

        if self.sio_reconnect_wait_task != None:
            await self.sio_reconnect_wait_task

        if self.conn_led != None:
            self.conn_led.clear()
        if self.data_led != None:
            self.data_led.clear()

    def create_local_webrtc_track(self, topic:str):

        options = {"framerate": "30", "video_size": "640x480"}
        if self.wrtc_relay is None:
            if platform.system() == "Darwin":
                self.wrtc_webcam = MediaPlayer(
                    "default:none", format="avfoundation", options=options
                )
            elif platform.system() == "Windows":
                self.wrtc_webcam = MediaPlayer(
                    "video=Integrated Camera", format="dshow", options=options
                )
            else:
                for b in range(3,4):
                    for i in range(2,3):
                        dev = '/dev/bus/usb/00'+str(b)+'/00'+str(i)
                        # try:
                        self.wrtc_webcam = MediaPlayer(dev, options=options)
                        #     break
                        # except:
                        #     self.get_logger().error(dev+' failed')

            self.wrtc_relay = MediaRelay()

        return self.wrtc_relay.subscribe(self.wrtc_webcam.video)

    async def on_wrtc_offer(self, offerData:dict):

        if not 'sdp' in offerData.keys():
            return { 'err': 2, 'msg': 'Offer missing sdp' }
        if not 'type' in offerData.keys():
            return { 'err': 2, 'msg': 'Offer missing type' }

        offer = RTCSessionDescription(sdp=offerData["sdp"], type=offerData["type"])

        id_peer:str = None
        if 'id_app' in offerData.keys():
            id_peer = offerData['id_app']
        if 'id_instance' in offerData.keys():
            id_peer = offerData['id_instance']

        if id_peer == None:
            return { 'err': 2, 'msg': 'No valid peer id provided' }

        self.get_logger().info('Got offer from '+id_peer+': '+str(offer))

        if id_peer in self.wrtc_peer_pcs.keys():
            pc = self.wrtc_peer_pcs[id_peer]
        else:
            # config = RTCConfiguration(
            #     iceServers=[
            #         RTCIceServer(
            #             urls=[
            #                 "stun:stun.l.google.com:19302",
            #                 "stun:stun1.l.google.com:19302",
            #                 "stun:stun2.l.google.com:19302",
            #                 "stun:stun3.l.google.com:19302",
            #                 "stun:stun4.l.google.com:19302",
            #             ]
            #         ),
            #     ]
            # )
            config = RTCConfiguration(iceServers=[RTCIceServer(urls=['stun:stun.l.google.com:19302'])])
            pc = RTCPeerConnection(config)
            self.wrtc_peer_pcs[id_peer] = pc

        # open media source
        # video_track = ROSVideoStreamTrack()
        # data_track = ROSDataStreamTrack()

        # self.create_local_webrtc_track(topic='/camera/tbd')
        # if video_track:
        # video_sender:RTCRtpSender = pc.addTrack(video_track)

            # if args.video_codec:
            #     force_codec(pc, video_sender, args.video_codec)
            # elif args.play_without_decoding:
            #     raise Exception("You must specify the video codec using --video-codec")

        # offer:RTCSessionDescription = await pc.createOffer()

        # @self.data_sender.on('open')
        # async def on_sender_on():
        #     print('SENDER ON! Sending tests...')
        #     self.data_sender.send("Hello, is there anybody out there? 1")
        #     self.data_sender.send("Hello, is there anybody out there? 2" )
        #     self.data_sender.send("Hello, is there anybody out there? 3")

        print(f'IceConnectionState: {pc.iceConnectionState} IceGatheringState: {pc.iceGatheringState}')

        await pc.setRemoteDescription(offer)

        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            print(f"WebRTC Connection (peer={id_peer}) state is %s" % pc.connectionState)

            if pc.connectionState == "failed":
                await pc.close()
                self.wrtc_peer_pcs.pop(id_peer)

        @pc.on("icegatheringstatechange")
        async def on_icegatheringstatechange():
            print(f"WebRTC icegatheringstatechange (peer={id_peer}) state is %s" % pc.iceGatheringState)

        @pc.on("signalingstatechange")
        async def on_signalingstatechange():
            print('signalingstatechange', str(pc.signalingState))

        @pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            print(f"WebRTC iceconnectionstatechange (peer={id_peer}) state is %s" % pc.iceConnectionState)

        # async def ice_checker():
        #     while pc.iceGatheringState != 'complete':
        #         await asyncio.sleep(.1)
        #     self.get_logger().warn(f"Unlocking!")

        # await ice_checker()

        # async with iceComplete:
        #     await iceComplete.wait()
        #     await iceComplete.release()

        # answer = await pc.createAnswer()
        #await pc.setLocalDescription(answer)

        self.get_logger().info('Generated answer: '+str(pc.localDescription))

        return { 'sdp': pc.localDescription.sdp, 'type':pc.localDescription.type }

        # open media source
        # audio, video = create_local_tracks(
        #     args.play_from, decode=not args.play_without_decoding
        # )

        # if audio:
        #     audio_sender = pc.addTrack(audio)
        #     if args.audio_codec:
        #         force_codec(pc, audio_sender, args.audio_codec)
        #     elif args.play_without_decoding:
        #         raise Exception("You must specify the audio codec using --audio-codec")

        # if video:
        #     video_sender = pc.addTrack(video)
        #     if args.video_codec:
        #         force_codec(pc, video_sender, args.video_codec)
        #     elif args.play_without_decoding:
        #         raise Exception("You must specify the video codec using --video-codec")

        # await pc.setRemoteDescription(offer)

        # answer = await pc.createAnswer()
        # await pc.setLocalDescription(answer)

    async def set_wrtc_answer(self, answer_data:dict):

        print('answer data: ', answer_data)

        id_peer:str = None
        if 'id_app' in answer_data.keys():
            id_peer = answer_data['id_app']
        if 'id_instance' in answer_data.keys():
            id_peer = answer_data['id_instance']

        if id_peer == None:
            return { 'err': 1, 'msg': 'No valid peer id provided' }

        if not id_peer in self.wrtc_peer_pcs.keys():
            return { 'err': 1, 'msg': 'Peer not found' }

        if not 'sdp' in answer_data.keys() or not 'type' in answer_data.keys():
            return { 'err': 1, 'msg': 'Invalid answer data' }

        answer:RTCSessionDescription = RTCSessionDescription(sdp=answer_data['sdp'], type=answer_data['type'])

        await self.wrtc_peer_pcs[id_peer].setRemoteDescription(answer)
        return { 'success': 1 }


def key_in_tuple_list(key:str, search_list:list[tuple]):
    for item in search_list:
        if item[0] == key:
            return True
    return False


def matches_param_filters(key:str, param:Parameter):
    for test in param.get_parameter_value().string_array_value:
        if test == '': continue

        #if re.search(test, key):
        #    return True
        if test == '.*' or test == key[0:len(test)]:
            return True
    return False

async def spin_async(node: BridgeController):

    cancel = node.create_guard_condition(lambda: None)

    def _spin(node: Node,
              future: asyncio.Future,
              event_loop: asyncio.AbstractEventLoop):
        while rclpy.ok and not future.cancelled() and not node.shutting_down_:
            try:
                rclpy.spin_once(node)
            except:
                pass
        if not future.cancelled():
            event_loop.call_soon_threadsafe(future.set_result, None)

    node.spin_task = node.event_loop.create_future()
    node.spin_thread = threading.Thread(target=_spin, args=(node, node.spin_task, node.event_loop))
    node.spin_thread.start()
    try:
        await node.spin_task
    except:
        pass

    cancel.trigger()

    node.spin_thread.join()
    node.destroy_guard_condition(cancel)


async def main_async(bridge_node:BridgeController):
    # create a node without any work to do

    # create tasks for spinning and sleeping
    spin_task = asyncio.get_event_loop().create_task(spin_async(bridge_node))
    connect_task = asyncio.get_event_loop().create_task(bridge_node.start_sio_client(addr=bridge_node.sio_address, port=bridge_node.sio_port, path=bridge_node.sio_path))

    # concurrently execute both tasks
    await asyncio.wait([spin_task, connect_task], return_when=asyncio.ALL_COMPLETED)

    # cancel tasks
    if spin_task.cancel():
        await spin_task
    if connect_task.cancel():
        await connect_task


def main(args=None):

    rclpy.init(signal_handler_options=rclpy.SignalHandlerOptions.NO)
    rclpy.uninstall_signal_handlers() #duplicate?

    bridge_node = BridgeController()
    bridge_node.start_discovery()

    try:
        asyncio.get_event_loop().run_until_complete(main_async(bridge_node))
    except:
        pass

    asyncio.get_event_loop().run_until_complete(bridge_node.shutdown_cleanup())

    try:
        bridge_node.destroy_node()
        rclpy.shutdown()
    except:
        pass

    asyncio.get_event_loop().close()

if __name__ == '__main__':
    main()