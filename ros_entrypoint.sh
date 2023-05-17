#!/bin/bash
set -e

# setup ros environment
source "/opt/ros/humble/setup.bash"
test -f "/ros2_ws/install/setup.bash" && source "/ros2_ws/install/setup.bash"

exec "$@"
