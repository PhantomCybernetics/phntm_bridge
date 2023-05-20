#!/usr/bin/env python3

import rclpy
from rclpy.node import Node, Parameter, Subscription, QoSProfile
from rclpy.qos import QoSReliabilityPolicy
from .inc.status_led import StatusLED
import signal

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
        self.declare_parameter('sio_address', 'https://api.phntm.io/robot')
        self.declare_parameter('sio_port', 1337)
        self.declare_parameter('sio_connection_retry_sec', 2)
        self.declare_parameter('sio_ssl_verify', True)

        self.sio_address = self.get_parameter('sio_address').get_parameter_value().string_value
        self.sio_port = self.get_parameter('sio_port').get_parameter_value().integer_value
        self.sio_ssl_verify = self.get_parameter('sio_ssl_verify').get_parameter_value().bool_value
        if (self.sio_address == None or self.sio_address == ''): self.get_logger().error(f'Param sio_address not provided!')
        if (self.sio_port == None): self.get_logger().error(f'Param sio_port not provided!')

        # Comm LED
        self.declare_parameter('conn_led_topic', '' )
        self.conn_led_topic = self.get_parameter('conn_led_topic').get_parameter_value().string_value
        self.conn_led = None
        if (self.conn_led_topic != None and self.conn_led_topic != ''):
            self.get_logger().info(f'CONN Led uses {self.conn_led_topic}')
            self.conn_led = StatusLED('conn', self.conn_led_topic)
            self.conn_led.set_disconnected()
            #self.led_spinner = self.create_timer(0.1, lambda: rclpy.spin_once(self.status_led))

        # Net LED
        self.declare_parameter('data_led_topic', '' )
        self.data_led_topic = self.get_parameter('data_led_topic').get_parameter_value().string_value
        self.data_led = None
        if (self.data_led_topic != None and self.data_led_topic != ''):
            self.get_logger().info(f'DATA Led uses {self.data_led_topic}')
            self.data_led = StatusLED('data', self.data_led_topic)
            self.data_led.stop()

        # TOPIC DICOVERY
        self.declare_parameter('discovery_period_sec', 5.0)
        self.declare_parameter('topic_whitelist', [ '.*' ])
        self.declare_parameter('topic_blacklist', [ '/led/', '/_', '/rosout' ] )
        self.declare_parameter('param_whitelist', [ '.*' ])
        self.declare_parameter('param_blacklist', [ '' ])

        self.discovered_topics_ = []
        self.subscribed_topics_ = []
        self.subscriptions_ = { str: [ Subscription, int ] }

        self.get_logger().info(f'Phntm WebRTC Bridge started, idRobot={self.id_robot}')

        self.discovery_timer_ = self.create_timer(self.get_parameter('discovery_period_sec').get_parameter_value().double_value, self.discovery_timer)
        self.discovery_timer()


    async def start_sio_client(self, addr:str, port:int):

        sio = socketio.AsyncClient(handle_sigint=True,
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
            self.get_logger().warn('Auth reply: ' + str(data))
            if self.conn_led != None:
                self.conn_led.set_connected()
                self.is_connected_ = True

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
                self.conn_led.set_disconnected()
                self.is_connected_ = False

        @sio.on('*')
        async def catch_all(event, data):
            self.get_logger().warn('Socket.io event ' + str(event) + ' data: ' + str(data))

        # @self.eio.on('error')
        # async def on_error():
        #     print('socket.io error')

        self.sio = sio
        self.get_logger().info(f'Socket.io connecting to {addr}:{port}')

        while not self.is_connected_ and not self.shutting_down_:
            try:
                self.get_logger().info("Connecting...");
                await self.sio.connect(url=f'{addr}:{port}', socketio_path='/robot/socket.io/')
                # await self.sio.wait()
            except socketio.exceptions.ConnectionError:
                self.get_logger().info('Socket.io connection error, will retry in 2...')
                await asyncio.sleep(2)
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


    def discovery_timer(self):
        discovered_topics = self.get_topic_names_and_types()
        # self.get_logger().info(f'Seeing {len(discovered_topics)} topics...')
        for topic in discovered_topics:
            if not key_in_list(topic[0], self.discovered_topics_):
                # self.get_logger().info(f'Discovered topic {topic[0]}')
                self.discovered_topics_.append(topic)

                if not key_in_list(topic[0], self.subscribed_topics_) \
                   and matches_param_filters(topic[0], self.get_parameter('topic_whitelist')) \
                   and not matches_param_filters(topic[0], self.get_parameter('topic_blacklist')):
                    self.topic_subscribe(topic)
                else:
                    self.get_logger().info(f'Ignoring topic {topic[0]} (filters)')

    def shutdown_cleanup(self):
        self.get_logger().warn('SHUTTING DOWN')
        self.shutting_down_ = True

        self.sio.disconnect()

        if self.conn_led != None:
            self.conn_led._off()
        if self.data_led != None:
            self.data_led._off()


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

async def spin_async(node: Node):
    cancel = node.create_guard_condition(lambda: None)
    def _spin(node: Node,
              future: asyncio.Future,
              event_loop: asyncio.AbstractEventLoop):
        while not future.cancelled():
            rclpy.spin_once(node)
        if not future.cancelled():
            event_loop.call_soon_threadsafe(future.set_result, None)
    event_loop = asyncio.get_event_loop()
    spin_task = event_loop.create_future()
    spin_thread = threading.Thread(target=_spin, args=(node, spin_task, event_loop))
    spin_thread.start()
    try:
        await spin_task
    except asyncio.CancelledError:
        cancel.trigger()
    spin_thread.join()
    node.destroy_guard_condition(cancel)


async def main_async(bridge_node:BridgeController):
    # create a node without any work to do

    # create tasks for spinning and sleeping
    spin_task = asyncio.get_event_loop().create_task(spin_async(bridge_node))
    # sleep_task = asyncio.get_event_loop().create_task(asyncio.sleep(5.0))
    connect_task = asyncio.get_event_loop().create_task(bridge_node.start_sio_client(addr=bridge_node.sio_address, port=bridge_node.sio_port))

    # concurrently execute both tasks
    try:
        await asyncio.wait([spin_task, connect_task], return_when=asyncio.ALL_COMPLETED)
    except:
        bridge_node.shutdown_cleanup()

    # cancel tasks
    if spin_task.cancel():
        await spin_task
    if connect_task.cancel():
       await connect_task


def main(args=None):
    rclpy.init()
    bridge_node = BridgeController()

    rclpy.uninstall_signal_handlers()

    asyncio.get_event_loop().run_until_complete(main_async(bridge_node))
    asyncio.get_event_loop().close()
    try:
        bridge_node.destroy_node()
        rclpy.shutdown()
    except:
        pass

    # try:
    #     rclpy.spin(bridge_node)
    # except KeyboardInterrupt:
    #     pass


if __name__ == '__main__':
    main()