#!/bin/bash
set -e

# setup ros environment
source "/opt/ros/humble/setup.bash" 

exec "$@"
