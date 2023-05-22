#!/usr/bin/env python3

import rclpy
from rclpy.node import Node, Parameter, Subscription, QoSProfile
from rclpy.qos import QoSReliabilityPolicy
from .inc.status_led import StatusLED
import signal
import time

# from rclpy.subscription import TypeVar
from ros2topic.api import get_msg_class

import asyncio
# import engineio
import threading
import socketio
# from websockets.sync.client import connect


class BridgeController(Node):
    def __init__(self):
        super().__init__('webrtc_bridge')

        self.is_connected_ = False
        self.shutting_down_ = False

        self.event_loop = asyncio.get_event_loop()

        # ID ROBOT
        self.declare_parameter('id_robot', '')
        self.declare_parameter('key', '')
        self.id_robot = self.get_parameter('id_robot').get_parameter_value().string_value
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

        self.discovered_topics_ = []
        self.subscribed_topics_ = []
        self.subscriptions_ = { str: [ Subscription, int ] }

        self.spin_thread: threading.Thread = None
        self.spin_task: asyncio.Future[any] = None
        self.sio_wait_task: asyncio.Future[any] = None
        self.sio_reconnect_wait_task: asyncio.Future[any] = None

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

        # self.get_logger().info('report_topics()!')

        data = []

        for discoveredTopicData in self.discovered_topics_:
            topic = discoveredTopicData[0]
            topic_data = [
                topic,
                key_in_list(topic, self.subscribed_topics_), #subsribed
            ]
            for msg_type in discoveredTopicData[1]:
                topic_data.append(msg_type)
            data.append(topic_data)

        await self.sio.emit(
            event='topics',
            data=data,
            callback=None
            )


    async def start_sio_client(self, addr:str, port:int, path:str):

        sio = socketio.AsyncClient(handle_sigint=False,
                                   logger=True,
                                   ssl_verify=self.sio_ssl_verify
                                   )

        @sio.on('connect')
        async def on_connect():
            self.get_logger().warn('Socket.io connection established')
            await sio.emit(
                event='auth',
                data={ 'id': self.id_robot, 'key': self.auth_key },
                callback=on_auth_reply
            )

        async def on_auth_reply(data):
            if  not 'err' in data.keys():
                self.get_logger().info('Auth successful: ' + str(data))
                self.is_connected_ = True
                if self.conn_led != None:
                    self.conn_led.on()
                await asyncio.get_event_loop().create_task(self.report_topics())
                #self asyncio.get_event_loop().
            else:
                self.get_logger().error('Auth failure: ' + str(data))
                await self.sio.disconnect()

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
                await self.sio.connect(url=f'{addr}:{port}', socketio_path=path)
                self.sio_wait_task = asyncio.get_event_loop().create_task(self.sio.wait())
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


    def topic_subscribe(self, topic:tuple[str, list[str]]):

        # for topic_msg_type_str in topic[1]:
        message_class = None
        try:
            message_class = get_msg_class(self, topic[0])
        except:
            pass

        if message_class == None:
            self.get_logger().error(f'NOT subscribing to topic {topic[0]} {topic[1]} (msg class not found)')
            return None

        qosProfile = QoSProfile(depth=1, reliability=QoSReliabilityPolicy.BEST_EFFORT)

        self.get_logger().warn(f'Subscribing to topic {topic[0]} {message_class}')
        self.subscriptions_[topic[0]] = [ self.create_subscription(
            message_class,
            topic[0],
            lambda msg: self.topic_subscriber_callback(topic[0], msg),
            qosProfile), 0]
        self.subscribed_topics_.append(topic)


    def topic_subscriber_callback(self, topic, msg):
        self.subscriptions_[topic][1] += 1

        if self.is_connected_ and topic != self.data_led_topic and self.data_led != None:
            self.data_led.once()

        if self.subscriptions_[topic][1] == 1:
            self.get_logger().info(f'Receiving {type(msg).__name__} from {topic}')

            # vif topic == '/joy' and self.conn_led != None:
            #    self.is_connected_ = True


            # if topic == '/cmd_vel' and self.conn_led != None:
            #    self.conn_led.set_disconnected()


    async def discover_topics(self):
        report_change = False
        discovered_topics = self.get_topic_names_and_types()
        self.get_logger().info(f'Seeing {len(discovered_topics)} topics...')

        for topic in discovered_topics:
            if not key_in_list(topic[0], self.discovered_topics_):
                # self.get_logger().info(f'Discovered topic {topic[0]}')
                self.discovered_topics_.append(topic)
                report_change = True

                if not key_in_list(topic[0], self.subscribed_topics_) \
                    and matches_param_filters(topic[0], self.get_parameter('topic_whitelist')) \
                    and not matches_param_filters(topic[0], self.get_parameter('topic_blacklist')):
                    self.topic_subscribe(topic)
                    report_change = True
                else:
                    self.get_logger().debug(f'Ignoring topic {topic[0]} (filters)')

        if report_change:
            #event_loop = asyncio.get_event_loop()
            #if event_loop != None:
            # self.get_logger().warn("UPDATING TOPICS")
            self.event_loop.create_task(self.report_topics())
            #else:
           #     self.get_logger().error('couldnt report_topics()')


    async def shutdown_cleanup(self):
        self.get_logger().warn('SHUTTING DOWN')
        self.shutting_down_ = True

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


def key_in_list(key:str, search_list:list):
    for t in search_list:
        if t[0] == key:
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