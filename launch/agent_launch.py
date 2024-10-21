from launch import LaunchDescription
from launch_ros.actions import Node
import os
from ament_index_python.packages import get_package_share_directory

def generate_launch_description():
    ld = LaunchDescription()

    agent_config = os.path.join(
        '/ros2_ws/',
        'phntm_agent_params.yaml'
        )
    
    agent_node = Node(
            package='phntm_bridge',
            executable='agent',
            output='screen',
            emulate_tty=True,
            parameters=[agent_config]
        )

    return LaunchDescription([
        
       agent_node
       
    ])
