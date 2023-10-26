ARG ROS_DISTRO=humble

FROM --platform=linux/amd64 ros:$ROS_DISTRO

# # arm64 only
# RUN echo 'deb [arch=arm64] http://ports.ubuntu.com/ focal main multiverse universe \n \
# deb [arch=arm64] http://ports.ubuntu.com/ focal-security main multiverse universe \n \
# deb [arch=arm64] http://ports.ubuntu.com/ focal-backports main multiverse universe \n \
# deb [arch=arm64] http://ports.ubuntu.com/ focal-updates main multiverse universe' >> /etc/apt/sources.list

RUN apt-get update -y --fix-missing
RUN apt-get install -y ssh \
                       vim mc \
                       iputils-ping net-tools iproute2 curl \
                       pip

RUN pip install --upgrade pip # aiorc neeed pip update or fails on cffi version inconsistency

RUN pip install setuptools==58.2.0 \
                Pillow \
                termcolor

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

# do ros things

RUN apt-get install -y xvfb

RUN echo '#!/bin/sh \n \
\n\
XVFB=/usr/bin/Xvfb \n\
XVFBARGS=":0 -screen 0 1024x768x24 -ac +extension GLX +render -noreset -nolisten tcp" \n\
PIDFILE=/var/run/xvfb.pid \n\
case "$1" in \n\
  start) \n\
    echo -n "Starting virtual X frame buffer: Xvfb" \n\
    start-stop-daemon --start --quiet --pidfile $PIDFILE --make-pidfile --background --exec $XVFB -- $XVFBARGS \n\
    echo "." \n\
    ;; \n\
  stop) \n\
    echo -n "Stopping virtual X frame buffer: Xvfb" \n\
    start-stop-daemon --stop --quiet --pidfile $PIDFILE \n\
    echo "." \n\
    ;; \n\
  restart) \n\
    $0 stop \n\
    $0 start \n\
    ;; \n\
  *) \n\
        echo "Usage: /etc/init.d/xvfb {start|stop|restart}" \n\
        exit 1 \n\
esac \n\
\n\
exit 0' > /etc/init.d/xvfb
RUN chmod a+x /etc/init.d/xvfb

# control
RUN apt-get install -y  ros-$ROS_DISTRO-ros2-control \
                        ros-$ROS_DISTRO-ros2-controllers \
                        ros-$ROS_DISTRO-ros2controlcli

RUN apt-get update -y --fix-missing

RUN apt-get install -y python3-vcstool python3-rosdep

RUN python3 -m pip install urdf2webots

# webots
RUN mkdir -p /etc/apt/keyrings
RUN wget -O /etc/apt/keyrings/Cyberbotics.asc https://cyberbotics.com/Cyberbotics.asc
RUN echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/Cyberbotics.asc] https://cyberbotics.com/debian binary-amd64/" | tee /etc/apt/sources.list.d/Cyberbotics.list
RUN apt-get update -y
RUN apt-get install webots -y

WORKDIR $ROS_WS
RUN --mount=type=bind,source=./webots_ros2,target=/ros2_ws/src/webots_ros2 \
    . /opt/ros/$ROS_DISTRO/setup.sh && \
    rosdep update --rosdistro $ROS_DISTRO && \
    rosdep install -i --from-path src --rosdistro $ROS_DISTRO -y
    # colcon build --symlink-install


# cd /ros2_ws
# rosdep update --rosdistro $ROS_DISTRO
# rosdep install --ignore-src --from-path src/webots_ros2_husarion/webots_ros2_husarion/ --rosdistro $ROS_DISTRO -y
# colcon build --base-paths src/webots_ros2_husarion/webots_ros2_husarion/rosbot_ros
# colcon build --base-paths src/webots_ros2_husarion/webots_ros2_husarion/rosbot_xl_ros
# colcon build --base-paths src/webots_ros2_husarion/webots_ros2_husarion/ros_components_description
# colcon build --base-paths src/webots_ros2_husarion/webots_ros2_husarion/webots_ros2_husarion
# colcon build --base-paths src/webots_ros2_husarion/webots_ros2_msgs
# !! colcon build --base-paths src/webots_ros2_husarion/webots_ros2_driver


# webots
# RUN apt-get install -y ros-$ROS_DISTRO-webots-ros2

# SHELL ["/bin/bash", "--login", "-c"]
# RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.35.3/install.sh | bash \
    # && nvm install 8





# /ros2_ws/ STILL NEEDS TO BE BUILT with colcon build --symlink-install




# ENV NVM_DIR /root/.nvm
# ENV NODE_VERSION 8.17

# # Replace shell with bash so we can source files
# RUN rm /bin/sh && ln -s /bin/bash /bin/sh

# # Install nvm with node and npm
# RUN mkdir -p $NVM_DIR
# RUN /bin/sh curl https://raw.githubusercontent.com/creationix/nvm/v0.20.0/install.sh | bash \
#     && . $NVM_DIR/nvm.sh \
#     && nvm install $NODE_VERSION \
#     && nvm alias default $NODE_VERSION \
#     && nvm use default

# ENV NODE_PATH $NVM_DIR/v$NODE_VERSION/lib/node_modules
# ENV PATH      $NVM_DIR/v$NODE_VERSION/bin:$PATH

# ENV WEBOTS_CONT_FOLDER=/webots_shared
# RUN mkdir -p $WEBOTS_CONT_FOLDER

# # RUN apt-get install -y ros-$ROS_DISTRO-webots-ros2 but with less bs
# RUN apt-get install -y ros-$ROS_DISTRO-builtin-interfaces \
#                        ros-$ROS_DISTRO-rclpy \
#                        ros-$ROS_DISTRO-std-msgs \
#                        ros-$ROS_DISTRO-webots-ros2-control \
#                        ros-$ROS_DISTRO-webots-ros2-driver \
#                        ros-$ROS_DISTRO-webots-ros2-importer \
#                        ros-$ROS_DISTRO-webots-ros2-msgs \
#                        ros-$ROS_DISTRO-ros-workspace \
#                        ros-$ROS_DISTRO-webots



# pimp up prompt with hostame and color
RUN echo "PS1='\${debian_chroot:+(\$debian_chroot)}\\[\\033[01;35m\\]\\u@\\h\\[\\033[00m\\] \\[\\033[01;34m\\]\\w\\[\\033[00m\\] 🐞 '"  >> /root/.bashrc

# ENTRYPOINT ["/ros_entrypoint.sh"]
ENTRYPOINT ["/ros_entrypoint.sh"]
CMD [ "bash" ]