from launch import LaunchDescription, LaunchContext
from launch_ros.actions import Node
import os
from ament_index_python.packages import get_package_share_directory

from launch.actions import (DeclareLaunchArgument, EmitEvent, ExecuteProcess,
                            LogInfo, RegisterEventHandler, TimerAction)
from launch.conditions import IfCondition
from launch.event_handlers import (OnExecutionComplete, OnProcessExit,
                                OnProcessIO, OnProcessStart, OnShutdown)
from launch.events import Shutdown
from launch.substitutions import (EnvironmentVariable, FindExecutable,
                                LaunchConfiguration, LocalSubstitution,
                                PythonExpression)

def generate_launch_description():

    bridge_config = os.path.join(
        '/ros2_ws/',
        'phntm_bridge_params.yaml'
        )

    bridge_node = Node(
        package='phntm_bridge',
        executable='bridge',
        name='phntm_bridge',
        output='screen',
        emulate_tty=True,
        parameters=[bridge_config]
    )
    
    return LaunchDescription([
        bridge_node
    ])
