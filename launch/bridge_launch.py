from launch import LaunchDescription
from launch_ros.actions import Node
import os
from ament_index_python.packages import get_package_share_directory

def generate_launch_description():
    ld = LaunchDescription()

    config = os.path.join(
        '/ros2_ws/',
        'phntm_launch_params.yaml'
        )

    return LaunchDescription([

        Node(
            package='phntm_webrtc_bridge',
            executable='bridge',
            name='webrtc_bridge',
            output='screen',
            emulate_tty=True,
            parameters=[config]
        )
    ])