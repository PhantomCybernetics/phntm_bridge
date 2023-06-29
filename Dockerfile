ARG ROS_DISTRO=humble

FROM ros:$ROS_DISTRO

RUN apt-get update
RUN apt-get install -y ssh \
                       vim \
		       mc \
                       inetutils-ping \
                       pip

RUN pip install aiohttp \
                setuptools==58.2.0 \
                python-socketio
		# aiortc

# generate entrypoint script
RUN echo '#!/bin/bash \
set -e \
\
# setup ros environment \
source "/opt/ros/'$ROS_DISTRO'/setup.bash" \
test -f "/ros2_ws/install/setup.bash" && source "/ros2_ws/install/setup.bash" \
 \
exec "$@"' > /ros_entrypoint.sh
RUN chmod a+x /ros_entrypoint.sh

# source underlay on every login
RUN echo "source /opt/ros/$ROS_DISTRO/setup.bash" >> /root/.bashrc
RUN echo "test -f \"/ros2_ws/install/setup.bash\" && source \"/ros2_ws/install/setup.bash\" >> /root/.bashrc

# init workspace

ENV ROS_WS /ros2_ws
RUN mkdir -p $ROS_WS/src

WORKDIR $ROS_WS

# install aiortc fork from phntm github here
# RUN git clone git@github.com:PhantomCybernetics/aiortc.git phntm-aiortc

# select bash as default shell
#SHELL ["/bin/bash", "-c"]


# RUN --mount=type=bind,source=./,target=/ros2_ws/src/webrtc_bridge \
#    . /opt/ros/$ROS_DISTRO/setup.sh && \
#     rosdep update --rosdistro $ROS_DISTRO && \
#     rosdep install -i --from-path src --rosdistro $ROS_DISTRO -y

#RUN --mount=type=bind,source=./,target=/ros2_ws/src/webrtc_bridge \
#    . /opt/ros/$ROS_DISTRO/setup.sh && \
#    colcon build --symlink-install

# RUN . install/local_setup.bash
# RUN ros2 run phntm_bridge phntm_bridge

# pimp up prompt with hostame and color
RUN echo "PS1='\${debian_chroot:+(\$debian_chroot)}\\[\\033[01;35m\\]\\u@\\h\\[\\033[00m\\]:\\[\\033[01;34m\\]\\w\\[\\033[00m\\]\\$ '"  >> /root/.bashrc

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD [ "bash" ]
