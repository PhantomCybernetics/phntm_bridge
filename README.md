# Phantom Bridge

Fast WebRTC + Socket.io ROS2 Bridge written in Python for real-time data and video streaming, teleoperation, HRI, and remote robot monitoring. Comes with Docker Container control for the host machine, CPU and Wi-Fi monitoring, and [customizable Web Interface](https://github.com/PhantomCybernetics/bridge_ui). \
\
[See full documentation here](https://docs.phntm.io/bridge)

## Features
- ROS2 Topic and Service discovery
- Fast streamimg of binary ROS2 messages (in a out)
- Fast H.264 video streaming (hw or sw encodeded frames)
- Software encoded ROS2 Image messages streamed as H.264 video
- Docker container discovery and control
- Reliable ROS2 Service calls
- ROS2 Parameneters read/write API
- Extra ROS2 packages can be easily included for custom message type support
- Robot's Wi-Fi signal monitoring, scan & roaming
- File retreival from any running Docker container (such as URDF models)
- System Load and Docker Stats monitoring
- Standalone lightweight Bridge Agent for monitoring and management of various parts of a distributed system
- Connects P2P or via a TURN server when P2P link is not possible
- Multiple peers can connect to the same machine at a very low extra CPU cost
- ~5-10ms RTT on local network, 50ms+ RTT remote operation via a TURN server
- Works with rosbag and sims such as Gazebo or Webots

## Architecture
![Infrastructure map](https://github.com/PhantomCybernetics/phntm_bridge/blob/a1499ecf02909b20fa7101d9d415bca3d61ca667/docs/PHNTM%20Bridge%20Architecture.png)

# Install

### Install Docker, Docker Build & Docker Compose

E.g. on Debian/Ubuntu follow [these instructions](https://docs.docker.com/engine/install/debian/). Then add the current user to the docker group:
```bash
sudo usermod -aG docker ${USER}
# log out & back in
```

### Build the Docker Image
```bash
cd ~
git clone git@github.com:PhantomCybernetics/phntm_bridge.git phntm_bridge
cd phntm_bridge
ROS_DISTRO=humble; \
docker build -f Dockerfile -t phntm/bridge:$ROS_DISTRO \
  --build-arg ROS_DISTRO=$ROS_DISTRO \
  --build-arg ARCH=aarch64 \
  .
```

### Register a new Machine on Cloud Bridge
This registers a new robot on the [Cloud Bridge](https://github.com/PhantomCybernetics/cloud_bridge) and returns default config file you can edit further. Unique id_robot and key pair are generated in this step.
```bash
wget -O ~/phntm_bridge.yaml 'https://bridge.phntm.io:1337/robot/register?yaml' --no-check-certificate
```

### Examine and Modify the Config File
Default configutation file was created in ~/phntm_bridge.yaml \
The full list of configuration options can be found [here](https://docs.phntm.io/bridge/configuration).
```yaml
# TODO
```

### Add Bridge Service to your compose.yaml
Add phntm_bridge service to your compose.yaml file with ~/phntm_bridge.yaml mounted in the container:
```yaml
services:
 phntm_bridge:
    image: phntm/bridge:humble
    container_name: phntm-bridge
    hostname: phntm-bridge.local
    restart: unless-stopped
    privileged: true
    network_mode: host
    cpuset: '0,1,2' # limit to cpu cores
    shm_size: 200m # more room for camera frames
    environment:
      - TERM=xterm
    volumes:
      - ~/phntm_bridge.yaml:/ros2_ws/phntm_bridge_params.yaml # config goes here
      - /var/run:/host_run # docker control needs this
      - /tmp:/tmp
    devices:
      - /dev:/dev # cameras need this
    command:
      ros2 launch phntm_bridge bridge_launch.py
```

### Launch the Bridge
```bash
docker compose up phntm_bridge
```

### Open the Web UI
Open https://bridge.phntm.io/ID_ROBOT in a web browser. \
 \
If you provided maintainer's e-mail in your robot's YAML config file, a permanent link will be sent to you for your reference after the first Bridge launch. \
 \
Please note that Firefox is not fully supported at this time, [reasons are explained here](https://github.com/PhantomCybernetics/bridge_ui/blob/main/FIREFOX_ISSUES.md).


## TODOs in the Pipeline
- Compressed CostMap streaming
- Compressed PointCloud streaming
- Audio in/out streaming
- Variable bitrate for video (?)
- Generic USB camera support (?!)


## See Also
- [Documentation](https://docs.phntm.io/bridge) Full Phantom Bridge documentation
- [Bridge UI](https://github.com/PhantomCybernetics/bridge_ui#readme) customizable robot web UI/dashboard
- [Picam ROS2](https://github.com/PhantomCybernetics/picam_ros2) standalone ROS node that converts hardware-encoded H.264 frames into ROS messages
- [Cloud Bridge](https://github.com/PhantomCybernetics/cloud_bridge#readme) facilitates peer handshakes and signalling