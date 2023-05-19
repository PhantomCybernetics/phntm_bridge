import rclpy
from rclpy.node import Node, Parameter, Subscription, QoSProfile
from rclpy.qos import QoSReliabilityPolicy
import sys
import re
from pydoc import locate
from .status_led import StatusLED


# from rclpy.subscription import TypeVar
from ros2topic.api import get_msg_class


class BridgeController(Node):
    def __init__(self):
        super().__init__('webrtc_bridge')

        self.declare_parameter('id_robot', '')
        self.declare_parameter('ws_server', 'api.phntm.io')
        self.declare_parameter('ws_port', 1337)

        self.declare_parameter('discovery_period_sec', 5.0)
        self.declare_parameter('topic_whitelist', [ '.*' ])
        self.declare_parameter('topic_blacklist', [ '/led/', '/_' ] )
        self.declare_parameter('param_whitelist', [ '.*' ])
        self.declare_parameter('param_blacklist', [ '' ])




        self.declare_parameter('conn_led_topic', '' )
        conn_led_topic = self.get_parameter('conn_led_topic').get_parameter_value().string_value
        if (conn_led_topic != None and conn_led_topic != ''):
            self.get_logger().info(f'CONN Led uses {conn_led_topic}')
            self.conn_led = StatusLED('conn', conn_led_topic)
            self.conn_led.set_disconnected()
            #self.led_spinner = self.create_timer(0.1, lambda: rclpy.spin_once(self.status_led))

        self.declare_parameter('data_led_topic', '' )
        data_led_topic = self.get_parameter('data_led_topic').get_parameter_value().string_value
        if (data_led_topic != None and data_led_topic != ''):
            self.get_logger().info(f'DATA Led uses {data_led_topic}')
            self.data_led = StatusLED('data', data_led_topic)
            self.data_led.stop()

        id_robot = self.get_parameter('id_robot').get_parameter_value().string_value
        if (id_robot == None or id_robot == ''):
            self.get_logger().error(f'Param id_robot not provided!')

        ws_server = self.get_parameter('ws_server').get_parameter_value().string_value
        ws_port = self.get_parameter('ws_port').get_parameter_value().integer_value

        if (ws_server == None or ws_server == ''):
            self.get_logger().error(f'Param ws_server not provided!')
        if (ws_port == None):
            self.get_logger().error(f'Param ws_port not provided!')

        self.get_logger().info(f'Phntm WebRTC Bridge, idRobot={id_robot}')
        self.get_logger().info(f'WS Server: {ws_server}:{ws_port}')

        self.discovered_topics_ = []
        self.subscribed_topics_ = []
        self.subscriptions_ = { str: [ Subscription, int ] }

        self.discovery_timer_ = self.create_timer(self.get_parameter('discovery_period_sec').get_parameter_value().double_value, self.discovery_timer)
        self.discovery_timer()


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

        if topic == '/joy' and self.data_led != None:
            self.data_led.once(.0001)

        if self.subscriptions_[topic][1] == 1:
            self.get_logger().info(f'Receiving {type(msg).__name__} from {topic}')

            if topic == '/joy' and self.conn_led != None:
                self.conn_led.set_connected()

            if topic == '/cmd_vel' and self.conn_led != None:
                self.conn_led.set_disconnected()


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


def main(args=None):
    rclpy.init()
    bridge_node = BridgeController()
    try:
        rclpy.spin(bridge_node)
    except KeyboardInterrupt:
        pass

    try:
        bridge_node.destroy_node()
        rclpy.shutdown()
    except:
        pass

if __name__ == '__main__':
    main()
