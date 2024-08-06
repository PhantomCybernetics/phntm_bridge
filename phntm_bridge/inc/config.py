from rclpy.node import Node, Parameter, QoSProfile, Publisher
from .status_led import StatusLED
from rclpy.callback_groups import CallbackGroup, MutuallyExclusiveCallbackGroup
from rclpy.impl.rcutils_logger import RcutilsLogger
import json

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

        #webrtc
        self.declare_parameter('ice_servers', [  'turn:turn.phntm.io:3478', 'turn:turn.phntm.io:3479'  ])
        self.declare_parameter('ice_username', 'robo')
        self.declare_parameter('ice_credential', 'pass')
        self.ice_servers = self.get_parameter('ice_servers').get_parameter_value().string_array_value
        self.ice_username = self.get_parameter('ice_username').get_parameter_value().string_value
        self.ice_credential = self.get_parameter('ice_credential').get_parameter_value().string_value
        
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
            # ROS stuffs
            self.declare_parameter(f'{topic_override}.name', '')
            self.declare_parameter(f'{topic_override}.durability', 0)
            self.declare_parameter(f'{topic_override}.raw', True)
            self.declare_parameter(f'{topic_override}.reliability', 0)
            self.declare_parameter(f'{topic_override}.lifespan', 1) #1s by default; -1 for infinity
            # NN stuffs
            self.declare_parameter(f'{topic_override}.nn_input_cropped_square', True) # nn input is usually a square
            self.declare_parameter(f'{topic_override}.nn_input_w', 416)
            self.declare_parameter(f'{topic_override}.nn_input_h', 416)
            self.declare_parameter(f'{topic_override}.nn_detection_labels', [ '' ]) # nn class labels
            # Depth processing
            self.declare_parameter(f'{topic_override}.depth_colormap', 13) # cv2.COLORMAP_MAGMA
            self.declare_parameter(f'{topic_override}.depth_range_max', 2.0) # 2m (units depend on sensor)
            # Battery
            self.declare_parameter(f'{topic_override}.min_voltage', 0.0)
            self.declare_parameter(f'{topic_override}.max_voltage', 10.0)
            
        self.declare_parameter('log_sdp', False)
        self.log_sdp = self.get_parameter('log_sdp').get_parameter_value().bool_value

        self.declare_parameter('log_heartbeat', False)
        self.log_heartbeat = self.get_parameter('log_heartbeat').get_parameter_value().bool_value
    
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

        # wifi monitoring + scan
        self.declare_parameter('iw_interface', 'wlan0')
        self.declare_parameter('iw_monitor_period_sec', 1.0)
        self.declare_parameter('iw_monitor_topic', '/iw_status')

        # picamera2
        self.declare_parameter('picam_enabled', False)
        self.picam_enabled = self.get_parameter('picam_enabled').get_parameter_value().bool_value
        self.declare_parameter('picam_hflip', False)
        self.declare_parameter('picam_vflip', False)
        self.declare_parameter('picam_bitrate', 5000000)
        self.declare_parameter('picam_framerate', 30)
        
        self.declare_parameter('ui_battery_topic', '/battery') # use this in ui 
        self.declare_parameter('ui_docker_control', True)
        self.docker_control_enabled = self.get_parameter('ui_docker_control').get_parameter_value().bool_value
        self.declare_parameter('ui_enable_wifi_scan', False)
        
        #input configs that get passed to ui
        self.declare_parameter('input_drivers', [ 'Joy' ]) # [ '' ] to disable input entirely
        self.declare_parameter(f'input_defaults', '') 
        
        self.input_drivers = self.get_parameter('input_drivers').get_parameter_value().string_array_value
        if len(self.input_drivers) == 0 or (len(self.input_drivers) == 1 and self.input_drivers[0] == ''):
            self.input_drivers = []
        
        input_defaults_file = self.get_parameter('input_defaults').get_parameter_value().string_value
        self.input_defaults = None
        
        if input_defaults_file:
            try:
                with open(input_defaults_file, "r") as read_content: 
                    self.input_defaults = json.load(read_content)
            except FileNotFoundError:
                logger.error(f'input_defaults file not found: {input_defaults_file}')
                pass
        