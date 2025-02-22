from rclpy.node import Node, Parameter, QoSProfile, Publisher
from .status_led import StatusLED
from rclpy.callback_groups import CallbackGroup, MutuallyExclusiveCallbackGroup
from rclpy.impl.rcutils_logger import RcutilsLogger
from rcl_interfaces.msg import ParameterDescriptor, ParameterType, FloatingPointRange, IntegerRange
import json

# tests
from nav_msgs.srv import GetPlan
from nav_msgs.msg import Path
from lifecycle_msgs.srv import ChangeState

class BridgeControllerConfig():

    def load_config(self, logger:RcutilsLogger):
    
        # self._make_test_srvs()
        # self._make_test_params()
        
        # ID ROBOT
        self.declare_parameter('id_robot', '', descriptor=ParameterDescriptor(
            type=ParameterType.PARAMETER_STRING,
            description='Robot ID on Phntm Cloud Brudge',
            read_only=True
        ))
        self.declare_parameter('key', '', descriptor=ParameterDescriptor(
            type=ParameterType.PARAMETER_STRING,
            description='Robot auth key on Phntm Cloud Brudge',
        ))
        self.id_robot = self.get_parameter('id_robot').get_parameter_value().string_value
        self.auth_key = self.get_parameter('key').get_parameter_value().string_value
        
        if (self.id_robot == None or self.id_robot == ''):
            self.get_logger().error(f'Param id_robot not provided!')
            exit(1)
        if (self.auth_key == None or self.auth_key == ''):
            logger.error(f'Param key not provided!')
            exit(1)
            
        self.declare_parameter('name', 'Unnamed Robot', descriptor=ParameterDescriptor(
            type=ParameterType.PARAMETER_STRING,
            description='Robot name'
        ))
        
        self.declare_parameter('maintainer_email', '', descriptor=ParameterDescriptor(
            type=ParameterType.PARAMETER_STRING,
            description='Maintainer e-mail address'
        ))

        # will check these packages on 1st (container) start
        self.declare_parameter('extra_packages', [  ''  ], descriptor=ParameterDescriptor(
            type=ParameterType.PARAMETER_STRING_ARRAY,
            description='ROS packages to check for on first Bridge run',
            additional_constraints='Folder path or ROS package name'
        ))
        self.extra_packages = self.get_parameter('extra_packages').get_parameter_value().string_array_value
        if len(self.extra_packages) == 1 and self.extra_packages[0] == '':
            self.extra_packages = []
        
        #webrtc
        self.declare_parameter('use_cloud_ice_config', True)
        self.use_cloud_ice_config = self.get_parameter('use_cloud_ice_config').get_parameter_value().bool_value
        self.declare_parameter('ice_servers', [  ''  ])
        self.declare_parameter('ice_username', '') # id_robot if empty
        self.declare_parameter('ice_secret', '')
        self.ice_servers_custom = self.get_parameter('ice_servers').get_parameter_value().string_array_value
        if len(self.ice_servers_custom) == 1 and self.ice_servers_custom[0] == '':
            self.ice_servers_custom = []
        else:
            logger.info(f'Custom ICE servers: {str(self.ice_servers_custom)}')
        self.ice_servers = self.ice_servers_custom.copy()
        
        self.ice_username = self.get_parameter('ice_username').get_parameter_value().string_value
        if self.ice_username == '':
            self.ice_username = self.id_robot
        self.ice_secret = self.get_parameter('ice_secret').get_parameter_value().string_value
        
        # prevent reading sensitive stuffs
        key_censored = Parameter('key', Parameter.Type.STRING, value='*************')
        ice_username_censored = Parameter('ice_username', Parameter.Type.STRING, value='*************')
        ice_secret_censored = Parameter('ice_secret', Parameter.Type.STRING, value='*************')
        self.set_parameters([ key_censored, ice_username_censored, ice_secret_censored]) 
        
        # Cloud Bridge host
        self.declare_parameter('cloud_bridge_address', 'https://us-ca.bridge.phntm.io')
        
        # Cloud Bridge files uploader port
        self.declare_parameter('file_upload_port', 1336)
        
        # Socket.io
        self.declare_parameter('sio_port', 1337)
        self.declare_parameter('sio_path', '/robot/socket.io')
        self.declare_parameter('sio_connection_retry_sec', 5.0)
        self.declare_parameter('sio_ssl_verify', True)
   
        # services collapsed in the ui menu (still operational, parameneter services by default; msg type or full service id)
        self.declare_parameter('collapse_services', [ 'rcl_interfaces/srv/DescribeParameters', 'rcl_interfaces/srv/GetParameterTypes', 'rcl_interfaces/srv/GetParameters', 'rcl_interfaces/srv/ListParameters', 'rcl_interfaces/srv/SetParameters', 'rcl_interfaces/srv/SetParametersAtomically' ], descriptor=ParameterDescriptor(
            type=ParameterType.PARAMETER_STRING_ARRAY,
            description='The UI will collapse these services',
            additional_constraints='Service id or type '
        ))
    
        self.declare_parameter('collapse_unhandled_services', True, descriptor=ParameterDescriptor(
            type=ParameterType.PARAMETER_BOOL,
            description='The UI will collapse services with unsupported message types'
        ))
        
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
        self.declare_parameter('log_heartbeat', False)
        self.declare_parameter('log_message_every_sec', 10.0)
        self.log_message_every_sec = self.get_parameter('log_message_every_sec').get_parameter_value().double_value

        self.cloud_bridge_address = self.get_parameter('cloud_bridge_address').get_parameter_value().string_value
        self.file_upload_port = self.get_parameter('file_upload_port').get_parameter_value().integer_value
        self.sio_port = self.get_parameter('sio_port').get_parameter_value().integer_value
        self.sio_path = self.get_parameter('sio_path').get_parameter_value().string_value
        self.sio_ssl_verify = self.get_parameter('sio_ssl_verify').get_parameter_value().bool_value
        self.sio_connection_retry_sec = self.get_parameter('sio_connection_retry_sec').get_parameter_value().double_value
        if (self.cloud_bridge_address == None or self.cloud_bridge_address == ''): self.get_logger().error(f'Param cloud_bridge_address not provided!')
        if (self.file_upload_port == None): logger.error(f'Param file_upload_port not provided!')
        if (self.sio_port == None): logger.error(f'Param sio_port not provided!')

        self.uploader_address = f'{self.cloud_bridge_address}:{self.file_upload_port}'
        
        # Conn LED control via topic (blinks when connecting; on when connected; off = bridge not running)
        self.declare_parameter('conn_led_topic', '' )
        self.conn_led_topic = self.get_parameter('conn_led_topic').get_parameter_value().string_value
        # Data LED control via topic (flashes when any data is sent via webrtc; off when not connected)
        self.declare_parameter('data_led_topic', '' )
        self.data_led_topic = self.get_parameter('data_led_topic').get_parameter_value().string_value
        
        # Conn/Data LED control via GPIO
        self.declare_parameter('conn_led_gpio_chip', '/dev/gpiochip4') # PI5 default
        self.conn_led_gpio_chip = self.get_parameter('conn_led_gpio_chip').get_parameter_value().string_value
        self.declare_parameter('conn_led_pin', -1) # set GPIO number
        self.conn_led_pin = self.get_parameter('conn_led_pin').get_parameter_value().integer_value
        self.declare_parameter('data_led_pin', -1) # set GPIO number
        self.data_led_pin = self.get_parameter('data_led_pin').get_parameter_value().integer_value

        # Discovery
        self.declare_parameter('discovery_period_sec', 5.0)
        self.declare_parameter('stop_discovery_after_sec', -1.0) # < 0 => never

        # self.declare_parameter('auto_queued_topics', [ '/tf', '/tf_static', '/robot_description' ])
        
        # wifi monitoring + scan
        self.declare_parameter('ui_wifi_monitor_topic', '/iw_status') # agent writes here
        self.declare_parameter('ui_enable_wifi_scan', True) # enables scan without roaming
        self.declare_parameter('ui_enable_wifi_roam', False) # enables roaming (potentially dangerous)
        
        self.declare_parameter('ui_battery_topic', '/battery') # use this in ui 
        self.declare_parameter('ui_docker_control', True)
        self.declare_parameter('docker_monitor_topic', '/docker_info')
        self.docker_control_enabled = self.get_parameter('ui_docker_control').get_parameter_value().bool_value
        
        #input configs that get passed to ui
        self.declare_parameter('input_drivers', [ 'Joy' ]) # [ '' ] to disable input entirely, services are still set up
        self.declare_parameter('custom_input_drivers', [ '' ])
        self.declare_parameter('input_defaults', '') 
        self.declare_parameter('service_defaults', '')
        self.declare_parameter('custom_service_widgets', [ '' ])
        self.declare_parameter('service_widgets', [ '' ])
        
        self.input_drivers = self.get_parameter('input_drivers').get_parameter_value().string_array_value
        if len(self.input_drivers) == 0 or (len(self.input_drivers) == 1 and self.input_drivers[0] == ''):
            self.input_drivers = []
            
        custom_input_drivers_str = self.get_parameter('custom_input_drivers').get_parameter_value().string_array_value
        self.custom_input_drivers = []
        for custom_driver_str in custom_input_drivers_str:
            if custom_driver_str.strip() == '':
                continue
            parts = custom_driver_str.split(' ')
            parts_filtered = []
            for p in parts:
                if p != '':
                    parts_filtered.append(p)
            if len(parts_filtered) == 2:
                self.custom_input_drivers.append({
                    'class': parts_filtered[0],
                    'url': parts_filtered[1],
                })
                logger.info(f'Adding custom input driver: {parts_filtered[0]} from {parts_filtered[1]}')
            else:
                logger.error(f'Invalid custom input driver definition: {custom_driver_str}')
                
        custom_service_widgets_str = self.get_parameter('custom_service_widgets').get_parameter_value().string_array_value
        self.custom_service_widgets = []
        for custom_service_widget_str in custom_service_widgets_str:
            if custom_service_widget_str.strip() == '':
                continue
            parts = custom_service_widget_str.split(' ')
            parts_filtered = []
            for p in parts:
                if p != '':
                    parts_filtered.append(p)
            if len(parts_filtered) == 2:
                self.custom_service_widgets.append({
                    'class': parts_filtered[0],
                    'url': parts_filtered[1],
                })
                logger.info(f'Adding custom service widget: {parts_filtered[0]} from {parts_filtered[1]}')
            else:
                logger.error(f'Invalid custom service widget definition: {custom_service_widget_str}')
        
        service_widgets_str = self.get_parameter('service_widgets').get_parameter_value().string_array_value
        self.service_widgets = []
        for service_widget_str in service_widgets_str:
            if service_widget_str.strip() == '':
                continue
            
            assign_part = service_widget_str
            json_data = None
            if service_widget_str.count('{'):
                json_data_start = service_widget_str.index('{')
                assign_part = service_widget_str[:json_data_start]
                data_part = service_widget_str[json_data_start:]
                try:
                    json_data = json.loads(data_part)
                except json.decoder.JSONDecodeError as e: 
                    logger.error(f'Error parsing widget JSON data, {e}: {data_part}')
                    break
            parts = assign_part.split(' ')
            parts_filtered = []
            for p in parts:
                if p != '':
                    parts_filtered.append(p)
            if len(parts_filtered) == 2:
                mapping = {
                    'srv': parts_filtered[0],
                    'class': parts_filtered[1]
                }
                if json_data:
                    mapping['data'] = json_data
                self.service_widgets.append(mapping)
                logger.info(f'Adding service widget mapping: {parts_filtered[0]} > {parts_filtered[1]} data={str(json_data)}')
            else:
                logger.error(f'Invalid service widget mapping: {service_widget_str}')

        input_defaults_file = self.get_parameter('input_defaults').get_parameter_value().string_value
        self.input_defaults = None
        if input_defaults_file:
            try:
                with open(input_defaults_file, "r") as read_content: 
                    self.input_defaults = json.load(read_content)
            except FileNotFoundError:
                logger.error(f'Input defaults file not found: {input_defaults_file}')
                pass
        service_defaults_file = self.get_parameter('service_defaults').get_parameter_value().string_value
        self.service_defaults = None
        if service_defaults_file:
            try:
                with open(service_defaults_file, "r") as read_content: 
                    self.service_defaults = json.load(read_content)
            except FileNotFoundError:
                logger.error(f'Service defaults file not found: {service_defaults_file}')
                pass
    
    # TODO remove this
    # def _make_test_srvs(self):
    #     self.test_srv_1 = self.create_service(GetPlan, f'/{self.get_name()}/test_srv_1', self.test_srv_1_cb)
    #     self.test_srv_2 = self.create_service(ChangeState, f'/{self.get_name()}/test_srv_2', self.test_srv_2_cb)
        
    # TODO remove this
    # def test_srv_1_cb(self, request, response):
    #     print(f'test_srv_1_cb called w request: {str(request)}')
    #     response.plan = Path()
    #     return response
    
    # TODO remove this
    # def test_srv_2_cb(self, request, response):
    #     print(f'test_srv_2_cb called w request: {str(request)}')
    #     response.success = True
    #     return response
    
    # TODO remove this
    # def _make_test_params(self):
    #     self.declare_parameter('test_bool', False, descriptor=ParameterDescriptor(
    #         type=ParameterType.PARAMETER_BOOL,
    #         description='This is a description of my bool parameter',
    #         additional_constraints='Keep it cool',
    #         read_only=True
    #     ))
    #     self.declare_parameter('test_bool_array', [ False ], descriptor=ParameterDescriptor(
    #         type=ParameterType.PARAMETER_BOOL_ARRAY,
    #         description='Bool array ftw',
    #         additional_constraints='true or false, it\'s simple'
    #     ))
    #     self.declare_parameter('test_byte_array', [], descriptor=ParameterDescriptor(
    #         type=ParameterType.PARAMETER_BYTE_ARRAY,
    #         description='Byte array this is'
    #     ))
    #     self.declare_parameter('test_double', 0.0, descriptor=ParameterDescriptor(
    #         type=ParameterType.PARAMETER_DOUBLE,
    #         floating_point_range=[ FloatingPointRange(
    #             from_value=-10.0,
    #             to_value=100.0
    #         ) ]
    #     ))
    #     self.declare_parameter('test_double_array', [ 0.0 ], descriptor=ParameterDescriptor(
    #         type=ParameterType.PARAMETER_DOUBLE_ARRAY,
    #         description='Array of floating point numbers',
    #         additional_constraints='Keep it between +/- 100',
    #     ))
    #     self.declare_parameter('test_integer', 0, descriptor=ParameterDescriptor(
    #         type=ParameterType.PARAMETER_INTEGER,
    #         integer_range=[ IntegerRange(
    #             from_value=-11,
    #             to_value=111
    #         ) ]
    #     ))
    #     self.declare_parameter('test_integer_array', [ 1 ], descriptor=ParameterDescriptor(
    #         type=ParameterType.PARAMETER_INTEGER_ARRAY,
    #         description='Int array',
    #         additional_constraints='Yo'
    #     ))
    #     self.declare_parameter('test_string', '', descriptor=ParameterDescriptor(
    #         type=ParameterType.PARAMETER_STRING,
    #         read_only=True
    #     ))
    #     self.declare_parameter('test_string_array', [ '' ], descriptor=ParameterDescriptor(
    #         type=ParameterType.PARAMETER_STRING_ARRAY,
    #         description='Insert your strings here',
    #         additional_constraints='For realz',
    #         read_only=False
    #     ))
        