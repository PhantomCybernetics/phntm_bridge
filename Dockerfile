ARG ROS_DISTRO=humble

FROM ros:$ROS_DISTRO

RUN apt-get update
RUN apt-get install -y ssh

COPY --chmod=a+x ./ros_entrypoint.sh /ros_entrypoint.sh

#ENV CC=clang
#ENV CXX=clang++

ENV ROS_WS /ros2_ws
RUN mkdir -p $ROS_WS/src

# select bash as default shell
#SHELL ["/bin/bash", "-c"]

WORKDIR $ROS_WS

RUN --mount=type=bind,source=./,target=/ros2_ws/src/phntm_bridge \
    . /opt/ros/$ROS_DISTRO/setup.sh && \
     rosdep update --rosdistro $ROS_DISTRO && \
     rosdep install -i --from-path src --rosdistro $ROS_DISTRO -y

RUN --mount=type=bind,source=./,target=/ros2_ws/src/phntm_bridge \
    . /opt/ros/$ROS_DISTRO/setup.sh && \
    colcon build --symlink-install

# RUN . install/local_setup.bash
# RUN ros2 run phntm_bridge phntm_bridge

RUN echo "source /opt/ros/$ROS_DISTRO/setup.bash" >> /root/.bashrc
RUN echo "source /ros2_ws/install/local_setup.bash" >> /root/.bashrc

ENTRYPOINT ["/ros_entrypoint.sh"]
CMD [ "bash" ]
