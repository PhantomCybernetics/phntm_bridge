ARG ROS_DISTRO=humble
FROM ros:$ROS_DISTRO

ARG PI_EXTRAS=False
ARG PI_CAMERA=False
ARG ARCH=aarch64

RUN echo "Building docker image with ROS_DISTRO=$ROS_DISTRO, ARCH=$ARCH, PI_EXTRAS=$PI_EXTRAS PI_VERSION=$PI_VERSION"

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

RUN pip install setuptools \
                python-socketio \
                opencv-python \
                termcolor \
                aiohttp \
                PyEventEmitter \
                gpiod
		        # aiortc #forked
                # aiohttp #forker

# raspi extras
RUN if [ "$PI_EXTRAS" = "True" ]; then \
        apt-get install -y libraspberrypi0 libraspberrypi-dev libraspberrypi-bin; \
    fi

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
# RUN echo "export PATH=\$PATH:/root/.local/bin" >> /root/.bashrc
ENV PATH=$PATH":/root/.local/bin"

# init workspace
ENV ROS_WS=/ros2_ws
RUN mkdir -p $ROS_WS/src

# kms++ from source (for picamera2) \
RUN apt-get install -y libdrm-common libdrm-dev
WORKDIR /opt
RUN git clone https://github.com/tomba/kmsxx.git
WORKDIR /opt/kmsxx
RUN git submodule update --init
ENV PYTHONPATH=$PYTHONPATH":/opt/kmsxx/build/py"
ENV LD_LIBRARY_PATH=$LD_LIBRARY_PATH":/usr/local/lib/"$ARCH"-linux-gnu"
RUN /root/.local/bin/meson build
RUN ninja -C build install

RUN apt-get install -y libcap-dev

# libcamera (makes its python bidings for picamera2)
WORKDIR /opt

# libcamera from src works on pi4b + bullseye
# libcamera v0.1.0 works with picamera2==0.3.12
RUN if [ "$PI_CAMERA" = "Old" ]; then \
    git clone https://git.libcamera.org/libcamera/libcamera.git; \
    cd /opt/libcamera; \
    git checkout v0.1.0; \
    /root/.local/bin/meson setup build -D pycamera=enabled -D v4l2=True --reconfigure; \
    ninja -C build install; \
    pip install picamera2==0.3.12; \
fi

# libcamera v0.2.0 from raspi fork works with picamera2==0.3.17 (current)
# works on pi5 w bookworm (sw only encoding)
RUN if [ "$PI_CAMERA" = "True" ]; then \
    git clone https://github.com/raspberrypi/libcamera.git; \
    cd /opt/libcamera; \
    /root/.local/bin/meson setup build -D pycamera=enabled -D v4l2=True --reconfigure; \
    ninja -C build install; \
    cd /opt; \
    git clone -b next https://github.com/PhantomCybernetics/picamera2.git; \
    pip install -e picamera2; \
fi
ENV PYTHONPATH=$PYTHONPATH":/opt/libcamera/build/src/py"

# needed by reload-devices.sh (reloads docker devices after the container has been created)
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

# clone and install phntm agent
# RUN git clone https://github.com/PhantomCybernetics/phntm_agent.git /ros2_ws/src/phntm_agent
# RUN . /opt/ros/$ROS_DISTRO/setup.sh && \
#     . /ros2_ws/install/setup.sh && \
#     rosdep install -i --from-path src/phntm_agent --rosdistro $ROS_DISTRO -y && \
#    colcon build --symlink-install --packages-select phntm_agent

# clone and install phntm bridge
# RUN git clone https://github.com/PhantomCybernetics/phntm_bridge.git /ros2_ws/src/phntm_bridge
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
