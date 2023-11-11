# Phantom Bridge

Fast WebRTC + Socket.io ROS2 bridge written in Python for real-time data and video streaming, teleoperation, HRI, and remote monitoring. Comes with Docker Container control for the host machine, CPU and Wifi monitoring, customizable Web UI and peer client API.

## Features
- ROS Topic and Service discovery 
- Fast streamimg of binary ROS2 messages via UDP 
- Fast hw-encoded H264 video steraming (Pi Cam + Pi, TODO: OAK, suggestions?) 
- Stream sw-encoded ROS Image messages as H264 video (CPU cost, 3-10 FPS max on Pi4B)  
- Docker container discovery & reliable control (start/stop/restart) via Socket.io  
- Reliable ROS service calls via Socket.io  
- Robot's wifi signal strength monitoring, scan & AP roaming  
- Connects P2P or via a TURN server  
- ~10-30ms RTT on the same wifi local network, ~50ms RTT remote operation  
- Multiple peers can connect to the same machine at very low extra CPU cost (incl. video streams)  
- Works with rosbag, and sims like Gazebo or Webots

TODO
- TODO: File upload from any running Docker container (STL, etc)  
- TODO: System load + Docker stats monitoring  
- TODO: Variable bitrate for video streams  
- TODO: USB camera support  

See also:
- See @cloud_bridge Cloud Bridge server facilitates peer handshakes and signalling  
- See @bridge_ui Web UI, customizabe dashboard for data+video stream visualization and interaction with a ROS-enabled systems in a web browser in real time  

# Install

### Install Docker & Docker Compose
```bash
sudo apt install docker docker-buildx docker-compose-v2
sudo usermod -aG docker ${USER}
#log out & back in
```

### Build Docker Image (TODO: docker pull)
```bash
cd ~
wget https://raw.githubusercontent.com/PhantomCybernetics/phntm_bridge/main/Dockerfile -O phntm-bridge.Dockerfile
docker build -f phntm-bridge.Dockerfile -t phntm/bridge:humble .
# docker download and builds several packages from source, this may take a while (about ~25 minutes on Pi 4B)
```

### Register new Machine on Cloud Bridge
This registers a new robot on the Cloud Bridge and returns default config file you can then edit further. Unique id_robot and key are generated in this step. More about the config file @here.
```bash
wget -O phntm_bridge.yaml 'https://bridge.phntm.io:1337/robot/register?yaml' --no-check-certificate
# edit the file to give your robot a name and configure the bridge
```

### Add service to compose.yaml
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

### Run
```bash
docker compose up phntm_bridge
```

Then open https://bridge.phntm.io/YOUR_ROBOT_ID in your web browser.
[The web UI is documented in detail here.](https://github.com/PhantomCybernetics/bridge_ui) (TODO: user tutorial)


# Dev Mode
Dev mode mapps live git repo on the host machine to the container so that you can make changes more conventinetly.
```bash
cd ~
git clone git@github.com:PhantomCybernetics/phntm_bridge.git phntm_bridge
```

Make the following changes to your docker compose service in compose.yaml. This overwrites /ros2_ws/src/phntm_bridge with live git repo so that you can edit source code from the host filesystem easily:
```yaml
services:
  phntm_bridge:
      - ~/phntm_bridge:/ros2_ws/src/phntm_bridge
    command:
      # launching manually to prevent Docker from exiting on crash
      /bin/sh -c "while sleep 1000; do :; done"
```

Launch manually for better control:
```bash
docker compose up phntm_bridge -d
docker exec -it phntm-bridge bash
ros2 launch phntm_bridge bridge_launch.py
```

# Video

## Hardware-encoded video

[Raspberry Pi Camera modules](https://www.raspberrypi.com/products/#cameras-and-displays) are automatically discovered out of the box and can stream very fast H264 video at high resolution with a very small CPU overhead. This is achieved utilizing hw-encoding capabilities on the VideoCore and Picam2 library included in the Docker image.

[OAK Cameras](https://shop.luxonis.com/collections/oak-cameras-1): TODO!

## ROS sensor_msgs/msg/Image

Standard ROS image topics can be subscribed to and streamed as WebRTC video. these will be software encoded and streamed as H.264. The ROS message contains a raw OpenCV frame, which needs to be encoded and packetized. At this moment the following frame encodings are impelemnted: rgb8 for RGB, 16UC1 and 32FC1 for depth.

Software encoding requires significantly more CPU time compared to GPU-based video encoding and can lead to increased latency and power consumption. Despite being offloaded to a dedicated process[^1]. On Pi 4B, camera streaming 640x480 @ 30 FPS only achieves about 5-10 FPS transmission. With delay between frames this long, every frame is encoded as a keyframe.

[^1] The process encoding image frames is in fact shared with all read subscriptions, including non-image ROS topics, in order ro isolate fast hw-encoded video streaming and inbound control data streams from potentially slower data.

## Depth processing

ROS Image messages containing depth data can be processed and colorized for better visibility. As mentioned above, 16UC1 and 32FC1 frame encoginds are supported at this point.

## USB cameras

TODO!




