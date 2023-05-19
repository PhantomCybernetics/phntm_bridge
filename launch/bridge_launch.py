from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            package='phntm_webrtc_bridge',
            executable='bridge',
            name='webrtc_bridge',
            output='screen',
            emulate_tty=True,
            parameters=[
                {'id_robot': 'aa186123aASAsaaklsjds322234'},
                {'ws_server': '192.168.1.67'},
                {'ws_port': 1338 },
                {'status_led_topic': '/led/right'}, # will blink here
            ]
        )
    ])