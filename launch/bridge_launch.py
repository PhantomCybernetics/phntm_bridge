from launch import LaunchDescription
from launch_ros.actions import Node
import os

def generate_launch_description():
    ld = LaunchDescription()

    config = os.path.join(
        '/ros2_ws/',
        'phntm_bridge_params.yaml'
        )

    return LaunchDescription([

        Node(
            package='phntm_bridge',
            executable='phntm_bridge',
            output='screen',
            emulate_tty=True,
            parameters=[config]
        )
    ])
