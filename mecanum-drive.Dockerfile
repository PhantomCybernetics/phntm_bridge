ARG ROS_DISTRO=humble

FROM ros:$ROS_DISTRO

RUN apt-get update -y --fix-missing
RUN apt-get install -y ssh \
                       vim mc \
                       iputils-ping net-tools iproute2 curl \
                       pip

# gazebo
# RUN apt install -y ros-$ROS_DISTRO-ros-gz

RUN pip install setuptools==58.2.0 \
                termcolor

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
RUN mkdir -p $ROS_WS/src/ros2-mecanum-bot/src

WORKDIR $ROS_WS/src/ros2-mecanum-bot/src
RUN git clone https://github.com/deborggraever/ros2-mecanum-bot.git

WORKDIR $ROS_WS

RUN  . /opt/ros/$ROS_DISTRO/setup.sh && \
     rosdep update --rosdistro $ROS_DISTRO && \
     rosdep install -i --from-path src --rosdistro $ROS_DISTRO -y -r && \
     colcon build

# pimp up prompt with hostame and color
RUN echo "PS1='\${debian_chroot:+(\$debian_chroot)}\\[\\033[01;35m\\]\\u@\\h\\[\\033[00m\\] \\[\\033[01;34m\\]\\w\\[\\033[00m\\] 🦄 '"  >> /root/.bashrc

WORKDIR $ROS_WS/src/phntm_bridge

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD [ "bash" ]
