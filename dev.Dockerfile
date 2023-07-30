ARG ROS_DISTRO=humble

FROM ros:$ROS_DISTRO

RUN apt-get update -y --fix-missing
RUN apt-get install -y ssh \
                       vim mc \
                       iputils-ping net-tools iproute2 curl \
                       pip

# aiorc neeed pip update or fails on cffi version inconsistency
RUN pip install --upgrade pip

# aiortc dev dependencies
RUN apt-get update -y --fix-missing
RUN apt-get install -y libavdevice-dev libavfilter-dev libopus-dev libvpx-dev pkg-config

# gazebo
# RUN apt install -y ros-$ROS_DISTRO-ros-gz

RUN pip install setuptools==58.2.0 \
                python-socketio \
                opencv-python \
                termcolor \
                aiohttp
		# aiortc #forked
                # aiohttp #forker

# generate entrypoint script
RUN echo '#!/bin/bash \n \
set -e \n \
\n \
# setup ros environment \n \
source "/opt/ros/'$ROS_DISTRO'/setup.bash" \n \
test -f "/ros2_ws/install/setup.bash" && source "/ros2_ws/install/setup.bash" \n \
\n \
exec "$@"' > /ros_entrypoint.sh

RUN chmod a+x /ros_entrypoint.sh

# source underlay on every login
RUN echo 'source /opt/ros/'$ROS_DISTRO'/setup.bash' >> /root/.bashrc
RUN echo 'test -f "/ros2_ws/install/setup.bash" && source "/ros2_ws/install/setup.bash"' >> /root/.bashrc

# init workspace

ENV ROS_WS /ros2_ws
RUN mkdir -p $ROS_WS/src

WORKDIR $ROS_WS

# install aiortc fork from phntm github here
# RUN git clone git@github.com:PhantomCybernetics/aiortc.git phntm-aiortc

# select bash as default shell
#SHELL ["/bin/bash", "-c"]

# mount forked ~/aiortc repo and install with pip
# needs rw access to regenerate src/aiortc/codecs/*.so

# mount forked ~/aioice repo and install with pip
RUN --mount=type=bind,rw=true,source=./aioice,target=/ros2_ws/aioice \
        pip install -e /ros2_ws/aioice

RUN --mount=type=bind,rw=true,source=./aiortc,target=/ros2_ws/aiortc \
        pip install -e /ros2_ws/aiortc

# mount ~/phntm_webrtc_bridge repo and buidl the pkg for the first time
RUN --mount=type=bind,source=./phntm_bridge,target=/ros2_ws/src/phntm_bridge \
        . /opt/ros/$ROS_DISTRO/setup.sh && \
        rosdep update --rosdistro $ROS_DISTRO && \
        rosdep install -i --from-path src --rosdistro $ROS_DISTRO -y && \
        colcon build --symlink-install --packages-select phntm_bridge

# RUN . install/local_setup.bash
# RUN ros2 run phntm_bridge phntm_bridge

# pimp up prompt with hostame and color
RUN echo "PS1='\${debian_chroot:+(\$debian_chroot)}\\[\\033[01;35m\\]\\u@\\h\\[\\033[00m\\] \\[\\033[01;34m\\]\\w\\[\\033[00m\\] 🦄 '"  >> /root/.bashrc

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD [ "bash" ]