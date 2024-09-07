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

        # will check these packages on 1st (container) start
        self.declare_parameter('extra_packages', [  ''  ])
        self.extra_packages = self.get_parameter('extra_packages').get_parameter_value().string_array_value
        if len(self.extra_packages) == 1 and self.extra_packages[0] == '':
            self.extra_packages = []
        self.declare_parameter('collapse_unhandled_services', True)
        
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
            self.declare_parameter(f'{topic_override}.reliability', 0) # 0 = best effort, 1 = reliable
            self.declare_parameter(f'{topic_override}.durability', 0) # 0 system default, 1 = transient local, 2 = volatile
            self.declare_parameter(f'{topic_override}.lifespan_sec', -1) # num sec as int, -1 infinity
            self.declare_parameter(f'{topic_override}.raw', True)
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
        logger.info(f'Loaded config topic_overrides: {str(self.topic_overrides)}')
        
        # TODO:
        # services collapsed in the ui menu (still operational, parameneter services by default; msg type or full service id)
        self.declare_parameter('collapse_services', [ 'rcl_interfaces/srv/DescribeParameters', 'rcl_interfaces/srv/GetParameterTypes', 'rcl_interfaces/srv/GetParameters', 'rcl_interfaces/srv/ListParameters', 'rcl_interfaces/srv/SetParameters', 'rcl_interfaces/srv/SetParametersAtomically' ])
        self.collapse_services = self.get_parameter('collapse_services').get_parameter_value().string_array_value
        if len(self.collapse_services) == 1 and self.collapse_services[0] == '':
            self.collapse_services = []
        
        # blacklist topics from discovery (msg type or full topic id)
        self.declare_parameter('blacklist_topics', [ '' ])
        self.blacklist_topics = self.get_parameter('blacklist_topics').get_parameter_value().string_array_value
        if len(self.blacklist_topics) == 1 and self.blacklist_topics[0] == '':
            self.blacklist_topics = []
        logger.info(f'Blacklisted topics: {str(self.blacklist_topics)}')
        
        # blacklist services from discovery (msg type or full topic id)
        self.declare_parameter('blacklist_services', [ '' ])
        self.blacklist_services = self.get_parameter('blacklist_services').get_parameter_value().string_array_value
        if len(self.blacklist_services) == 1 and self.blacklist_services[0] == '':
            self.blacklist_services = []
        logger.info(f'Blacklisted services: {str(self.blacklist_services)}')

        # blacklist msg types (topics/services are discovered but not deserialized or serialized)
        # pointcloud and costmap are here until fully suported (until then break browsers with too much unoptimized data)
        self.declare_parameter('blacklist_msg_types', [ 'sensor_msgs/PointCloud', 'sensor_msgs/msg/PointCloud2', 'cost_map_msgs/CostMap', 'nav_msgs/msg/OccupancyGrid' ])
        self.blacklist_msg_types = self.get_parameter('blacklist_msg_types').get_parameter_value().string_array_value
        if len(self.blacklist_msg_types) == 1 and self.blacklist_msg_types[0] == '':
            self.blacklist_msg_types = []
        logger.info(f'Blacklisted message types: {str(self.blacklist_msg_types)}')
        
        self.declare_parameter('log_sdp', False)
        self.log_sdp = self.get_parameter('log_sdp').get_parameter_value().bool_value

        self.declare_parameter('log_heartbeat', False)
        self.log_heartbeat = self.get_parameter('log_heartbeat').get_parameter_value().bool_value

        self.sio_address = self.get_parameter('sio_address').get_parameter_value().string_value
        self.sio_port = self.get_parameter('sio_port').get_parameter_value().integer_value
        self.sio_path = self.get_parameter('sio_path').get_parameter_value().string_value
        self.sio_ssl_verify = self.get_parameter('sio_ssl_verify').get_parameter_value().bool_value
        self.sio_connection_retry_sec = self.get_parameter('sio_connection_retry_sec').get_parameter_value().double_value
        if (self.sio_address == None or self.sio_address == ''): self.get_logger().error(f'Param sio_address not provided!')
        if (self.sio_port == None): logger.error(f'Param sio_port not provided!')

        # Conn LED (blinks when connecting; on when connected; off = bridge not running)
        self.declare_parameter('conn_led_topic', '' )
        self.conn_led_topic = self.get_parameter('conn_led_topic').get_parameter_value().string_value
        # TODO:
        self.declare_parameter('conn_led_pin', -1)
        self.conn_led_pin = self.get_parameter('conn_led_pin').get_parameter_value().integer_value

        # Data LED (flashes when any data is sent via webrtc; off when not connected)
        self.declare_parameter('data_led_topic', '' )
        self.data_led_topic = self.get_parameter('data_led_topic').get_parameter_value().string_value
        # TODO:
        self.declare_parameter('data_led_pin', -1)
        self.data_led_pin = self.get_parameter('data_led_pin').get_parameter_value().integer_value

        # Discovery
        self.declare_parameter('discovery_period_sec', 5.0)
        self.declare_parameter('stop_discovery_after_sec', -1.0) # < 0 => never

        # wifi monitoring + scan
        self.declare_parameter('ui_wifi_monitor_topic', '/iw_status') # agent writes here
        self.declare_parameter('ui_enable_wifi_scan', True) # enables scan without roaming
        self.declare_parameter('ui_enable_wifi_roam', False) # enables roaming (potentially dangerous)
         
        # picamera2
        self.declare_parameter('picam_enabled', False)
        self.picam_enabled = self.get_parameter('picam_enabled').get_parameter_value().bool_value
        self.declare_parameter('picam_hflip', False)
        self.declare_parameter('picam_vflip', False)
        self.declare_parameter('picam_bitrate', 5000000)
        self.declare_parameter('picam_framerate', 30)
        
        self.declare_parameter('ui_battery_topic', '/battery') # use this in ui 
        self.declare_parameter('ui_docker_control', True)
        self.declare_parameter('docker_monitor_topic', '/docker_info')
        self.docker_control_enabled = self.get_parameter('ui_docker_control').get_parameter_value().bool_value
        
        #input configs that get passed to ui
        self.declare_parameter('input_drivers', [ 'Joy' ]) # [ '' ] to disable input entirely, services are still set up
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
        