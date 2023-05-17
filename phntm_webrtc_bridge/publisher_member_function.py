# Copyright 2016 Open Source Robotics Foundation, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import rclpy
from rclpy.node import Node

from std_msgs.msg import String, Int32, Bool

class MinimalPublisher(Node):

    def __init__(self):
        super().__init__('minimal_publisher')
        self.publisher_ = self.create_publisher(String, '/talker_topic', 10)

        timer_period = 0.5  # seconds
        self.timer = self.create_timer(timer_period, self.timer_callback)
        self.i = 0

        self.led_state_ = False
        self.led_left_ = self.create_publisher(Bool, '/led/left', 10)
        self.led_right_ = self.create_publisher(Bool, '/led/right', 10)

    def timer_callback(self):

        # self.get_logger().info(f'Publishing...')
        try:
            msg = String()
            msg.data = 'Ohi!: %d' % self.i
            self.publisher_.publish(msg)

            self.i += 1

            msg_true = Bool()
            msg_true.data = True

            msg_false = Bool()
            msg_false.data = False

            self.led_left_.publish(msg_true if self.led_state_ else msg_false)
            self.led_right_.publish(msg_false if self.led_state_ else msg_true)

            self.led_state_ = not self.led_state_

            self.get_logger().info(f'Published: "%s", LED: {self.led_state_}' % msg.data)
        except:
             self.get_logger().info(f'Failed publishing')

    def clear_leds(self):
        self.get_logger().info('Clearing LEDs...')

        msg_false = Bool()
        msg_false.data = False

        self.led_left_.publish(msg_false)
        self.led_right_.publish(msg_false)

        self.get_logger().info('LEDS Clear')

def shutdown_cleanup():
    print('CONTEXT GOT SHUTDOWN')


def main(args=None):

    rclpy.init(args=args)

    minimal_publisher = MinimalPublisher()
    #context = rclpy.get_default_context()
    minimal_publisher.context.on_shutdown(shutdown_cleanup)

    # while not rclpy._shutdown():


    try:
        rclpy.spin(minimal_publisher)
    except KeyboardInterrupt:
        minimal_publisher.get_logger().info('Got KeyboardInterrupt, shutting down...')
        minimal_publisher.destroy_timer(minimal_publisher.timer)
        # rclpy.init(args=args)
        #minimal_publisher = MinimalPublisher()
        # minimal_publisher.clear_leds()

    # rclpy.spin_once(minimal_publisher)
    # Destroy the node explicitly
    # (optional - otherwise it will be done automatically
    # when the garbage collector destroys the node object)
    minimal_publisher.destroy_node()
    try:
        rclpy.shutdown()
    except:
        pass


if __name__ == '__main__':
    main()
