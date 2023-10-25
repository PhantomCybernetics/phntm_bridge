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
                aiohttp \
                PyEventEmitter
		# aiortc #forked
                # aiohttp #forker

#raspi
RUN apt-get install -y libraspberrypi0 libraspberrypi-dev libraspberrypi-bin

# video stuffs
RUN apt-get install -y v4l-utils ffmpeg

#libcamera deps
RUN pip3 install --user meson
RUN pip3 install --user --upgrade meson
RUN apt-get install -y ninja-build pkg-config
RUN apt-get install -y libyaml-dev python3-yaml python3-ply python3-jinja2
RUN apt-get install -y libudev-dev
RUN apt-get install -y libevent-dev
RUN apt-get install -y libgstreamer1.0-dev libgstreamer-plugins-base1.0-de
RUN echo "export PATH=\$PATH:/root/.local/bin" >> /root/.bashrc

# init workspace
ENV ROS_WS /ros2_ws
RUN mkdir -p $ROS_WS/src

# libcamera from src
WORKDIR $ROS_WS
RUN git clone https://git.libcamera.org/libcamera/libcamera.git
WORKDIR $ROS_WS/libcamera
RUN git checkout v0.1.0
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

# picamera2, 0.3.12 works with libcamera v0.1.0
RUN apt-get install -y libcap-dev
RUN pip install picamera2

# needed by reload-devies.sh (reloads docker devices after the container has been created)
RUN apt-get install -y udev

# fix numpy version to >= 1.25.2
RUN pip install numpy --force-reinstall

# docekr ctrl
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


WORKDIR $ROS_WS

# clone forked aioice repo and install with pip
RUN git clone https://github.com/PhantomCybernetics/aioice.git /ros2_ws/aioice
RUN pip install -e /ros2_ws/aioice

# install aiortc fork from phntm github
RUN git clone -b perf_fixes https://github.com/PhantomCybernetics/aiortc.git /ros2_ws/aiortc
RUN pip install -e /ros2_ws/aiortc

# clone and install phntm interfaces and bridge
RUN git clone https://github.com/PhantomCybernetics/phntm_interfaces.git /ros2_ws/src/phntm_interfaces
RUN . /opt/ros/$ROS_DISTRO/setup.sh && \
    rosdep update --rosdistro $ROS_DISTRO && \
    rosdep install -i --from-path src/phntm_interfaces --rosdistro $ROS_DISTRO -y && \
    colcon build --symlink-install --packages-select phntm_interfaces

RUN git clone https://github.com/PhantomCybernetics/phntm_bridge.git /ros2_ws/src/phntm_bridge
RUN . /opt/ros/$ROS_DISTRO/setup.sh && \
    . /ros2_ws/install/setup.sh && \
    rosdep install -i --from-path src/phntm_bridge --rosdistro $ROS_DISTRO -y && \
    colcon build --symlink-install --packages-select phntm_bridge

RUN --mount=type=bind,source=/dev,target=/dev \
    . /ros2_ws/src/phntm_bridge/scripts/reload-devices.sh

# pimp up prompt with hostame and color
RUN echo "PS1='\${debian_chroot:+(\$debian_chroot)}\\[\\033[01;35m\\]\\u@\\h\\[\\033[00m\\] \\[\\033[01;34m\\]\\w\\[\\033[00m\\] 🦄 '"  >> /root/.bashrc

WORKDIR $ROS_WS/src/phntm_bridge

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD [ "bash" ]