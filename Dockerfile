ARG ROS_DISTRO=humble

FROM ros:$ROS_DISTRO

RUN apt-get update
RUN apt-get install -y ssh \
                       vim \
                       pip

COPY --chmod=a+x ./ros_entrypoint.sh /ros_entrypoint.sh

#ENV CC=clang
#ENV CXX=clang++

ENV ROS_WS /ros2_ws
RUN mkdir -p $ROS_WS/src

# select bash as default shell
#SHELL ["/bin/bash", "-c"]

WORKDIR $ROS_WS

RUN --mount=type=bind,source=./,target=/ros2_ws/src/phntm_webrtc_bridge \
    . /opt/ros/$ROS_DISTRO/setup.sh && \
     rosdep update --rosdistro $ROS_DISTRO && \
     rosdep install -i --from-path src --rosdistro $ROS_DISTRO -y

RUN --mount=type=bind,source=./,target=/ros2_ws/src/phntm_webrtc_bridge \
    . /opt/ros/$ROS_DISTRO/setup.sh && \
    colcon build --symlink-install

# RUN . install/local_setup.bash
# RUN ros2 run phntm_bridge phntm_bridge

# hostame + purple prompts
RUN echo "phntm-bridge" > /etc/hostname
RUN echo "PS1='\${debian_chroot:+(\$debian_chroot)}\\[\\033[01;35m\\]\\u@\\h\\[\\033[00m\\]:\\[\\033[01;34m\\]\\w\\[\\033[00m\\]\\$ '"  >> /root/.bashrc

RUN echo "source /opt/ros/$ROS_DISTRO/setup.bash" >> /root/.bashrc
RUN echo "source /ros2_ws/install/local_setup.bash" >> /root/.bashrc

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD [ "bash" ]
