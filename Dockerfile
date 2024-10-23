ARG ROS_DISTRO=humble
FROM ros:$ROS_DISTRO

RUN echo "Building docker image with ROS_DISTRO=$ROS_DISTRO"

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

RUN pip install setuptools \
                python-socketio \
                opencv-python \
                termcolor \
                aiohttp \
                PyEventEmitter \
                gpiod

# init workspace
ENV ROS_WS=/ros2_ws
RUN mkdir -p $ROS_WS/src

# fix numpy version to >= 1.25.2
RUN pip install numpy --force-reinstall

# docker ctrl
RUN pip install docker

# wifi scanning
RUN apt-get install -y wireless-tools libiw-dev
RUN pip install iwlib
# wifi ctrl via shared /var/run/wpa_supplicant/ (also needs shared /tmp)
RUN apt-get install -y wpasupplicant

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

WORKDIR /opt

# clone forked aioice repo and install with pip
RUN git clone -b fixes https://github.com/PhantomCybernetics/aioice.git /opt/aioice
RUN pip install -e /opt/aioice

# install aiortc fork from phntm github
RUN git clone -b performance https://github.com/PhantomCybernetics/aiortc.git /opt/aiortc
RUN pip install -e /opt/aiortc

WORKDIR $ROS_WS

# clone and install phntm interfaces
RUN git clone https://github.com/PhantomCybernetics/phntm_interfaces.git /ros2_ws/src/phntm_interfaces
RUN . /opt/ros/$ROS_DISTRO/setup.sh && \
    rosdep update --rosdistro $ROS_DISTRO && \
    rosdep install -i --from-path src/phntm_interfaces --rosdistro $ROS_DISTRO -y && \
    colcon build --symlink-install --packages-select phntm_interfaces

# install phntm bridge and agent
COPY ./ $ROS_WS/src/phntm_bridge
RUN . /opt/ros/$ROS_DISTRO/setup.sh && \
    . /ros2_ws/install/setup.sh && \
    rosdep install -i --from-path src/phntm_bridge --rosdistro $ROS_DISTRO -y && \
    colcon build --symlink-install --packages-select phntm_bridge

# pimp up prompt with hostame and color
RUN echo "PS1='\${debian_chroot:+(\$debian_chroot)}\\[\\033[01;35m\\]\\u@\\h\\[\\033[00m\\] \\[\\033[01;34m\\]\\w\\[\\033[00m\\] 🦄 '"  >> /root/.bashrc

WORKDIR $ROS_WS/src/phntm_bridge

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD [ "bash" ]