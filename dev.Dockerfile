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

# modprobe bcm2835-v4l2 on host!!, then start container

#WORKDIR /root/
#RUN git clone https://github.com/raspberrypi/userland.git
#WORKDIR /root/userland
#RUN ./buildme --aarch64
#RUN echo "export LD_LIBRARY_PATH=/opt/vc/lib:$LD_LIBRARY_PATH" >> /root/.bashrc

#RUN apt-get install -y libcap-dev python3-prctl libcamera-dev
# RUN pip install picamera2
# RUN apt-get install -y libbcm2835-dev

#
RUN apt-get install -y ffmpeg

#raspi
RUN apt-get install -y libraspberrypi0 libraspberrypi-dev libraspberrypi-bin

RUN apt-get install -y v4l-utils

#libcamera deps
RUN pip3 install --user meson
RUN pip3 install --user --upgrade meson
RUN apt-get install -y ninja-build pkg-config
RUN apt-get install -y libyaml-dev python3-yaml python3-ply python3-jinja2
RUN apt-get install -y libudev-dev
RUN apt-get install -y libevent-dev
RUN apt-get install -y libgstreamer1.0-dev libgstreamer-plugins-base1.0-de
RUN echo "export PATH=\$PATH:/root/.local/bin" >> /root/.bashrc

# libcamera from src
WORKDIR $ROS_WS
RUN git clone https://git.libcamera.org/libcamera/libcamera.git
WORKDIR $ROS_WS/libcamera
RUN /root/.local/bin/meson setup build -D pycamera=enabled -D v4l2=True --reconfigure
RUN ninja -C build install
RUN echo "export PYTHONPATH=\$PYTHONPATH:/ros2_ws/libcamera/build/src/py" >> /root/.bashrc

# kms++ from source (for picamera2)
RUN apt-get install -y libdrm-common libdrm-dev
WORKDIR $ROS_WS
RUN git clone https://github.com/tomba/kmsxx.git
WORKDIR $ROS_WS/kmsxx
RUN git submodule update --init
RUN /root/.local/bin/meson build
RUN ninja -C build
RUN echo "export PYTHONPATH=\$PYTHONPATH:/ros2_ws/kmsxx/build/py" >> /root/.bashrc
RUN echo "export LD_LIBRARY_PATH=\$LD_LIBRARY_PATH:/usr/local/lib/aarch64-linux-gnu" >> /root/.bashrc

# picamera2 (picamera seems to fail on libmmal.so which is not awailable for arm64 atm)
RUN apt-get install -y libcap-dev
RUN pip install picamera2

# needed by reload-devies.sh (reloads docker devices after the container has been created)
RUN apt-get install -y udev

# fix numpy version to >= 1.25.2
RUN pip install numpy --force-reinstall

RUN pip install docker

# pimp up prompt with hostame and color
RUN echo "PS1='\${debian_chroot:+(\$debian_chroot)}\\[\\033[01;35m\\]\\u@\\h\\[\\033[00m\\] \\[\\033[01;34m\\]\\w\\[\\033[00m\\] 🦄 '"  >> /root/.bashrc

WORKDIR $ROS_WS/src/phntm_bridge

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD [ "bash" ]
