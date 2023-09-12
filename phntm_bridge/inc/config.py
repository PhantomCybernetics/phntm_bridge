from rclpy.node import Node, Parameter, QoSProfile, Publisher
from .status_led import StatusLED
from rclpy.callback_groups import CallbackGroup, MutuallyExclusiveCallbackGroup
from rclpy.impl.rcutils_logger import RcutilsLogger

class BridgeControllerConfig():

    def load_config(self, logger:RcutilsLogger):
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
            logger.error(f'Param key not provided!')
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

        self.declare_parameter('log_sdp', False)
        self.log_sdp = self.get_parameter('log_sdp').get_parameter_value().bool_value

        logger.info(f'Loaded config topic_overrides: {str(self.topic_overrides)}')

        self.sio_address = self.get_parameter('sio_address').get_parameter_value().string_value
        self.sio_port = self.get_parameter('sio_port').get_parameter_value().integer_value
        self.sio_path = self.get_parameter('sio_path').get_parameter_value().string_value
        self.sio_ssl_verify = self.get_parameter('sio_ssl_verify').get_parameter_value().bool_value
        self.sio_connection_retry_sec = self.get_parameter('sio_connection_retry_sec').get_parameter_value().double_value
        if (self.sio_address == None or self.sio_address == ''): self.get_logger().error(f'Param sio_address not provided!')
        if (self.sio_port == None): logger.error(f'Param sio_port not provided!')

        # Comm LED
        self.declare_parameter('conn_led_topic', '' )
        self.conn_led_topic = self.get_parameter('conn_led_topic').get_parameter_value().string_value

        # Net LED
        self.declare_parameter('data_led_topic', '' )
        self.data_led_topic = self.get_parameter('data_led_topic').get_parameter_value().string_value

        # TOPIC DICOVERY
        self.declare_parameter('discovery_period_sec', 5.0)
        self.declare_parameter('stop_discovery_after_sec', -1.0) # < 0 => never
        self.declare_parameter('topic_whitelist', [ '.*' ])
        self.declare_parameter('topic_blacklist', [ '/led/', '/_', '/rosout' ] )
        self.declare_parameter('param_whitelist', [ '.*' ])
        self.declare_parameter('param_blacklist', [ '' ])

        self.declare_parameter('iw_interface', 'wlan0')
        self.declare_parameter('iw_monitor_period_sec', 1.0)
        self.declare_parameter('iw_monitor_topic', '/iw_status')