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

- TODO: File upload from any running Docker container (STL, etc)
- TODO: System load + Docker stats monitoring
- TODO: Variable bitrate for video streams
- TODO: USB camera support

- See @cloud_bridge Cloud Bridge server facilitates peer handshakes and signalling
- See @bridge_ui Web UI, customizabe dashboard for data+video stream visualization and interaction with a ROS-enabled systems in a web browser in real time

### Install Docker & Compose
```
sudo apt install docker docker-buildx docker-compose-v2
```

## Register New Machine & Get Config from Cloud Bridge
```
wget -O phntm_bridge.yaml https://bridge.phntm.io:1337/robot/register?yaml
```
This registers a new robot on the Cloud Bridge and returns default config file you can then further edit. Unique id_robot and key are generated at this step. More about the config file @here.

## Install Phantom Bridge
```
cd ~
wget https://raw.githubusercontent.com/PhantomCybernetics/phntm_bridge/main/Dockerfile -O phntm-bridge.Dockerfile
docker build -f phntm-bridge.Dockerfile -t phntm/bridge:humble .
# docker download and builds several packages from source, this will take a minute
# TODO: docker pull
```

Add phntm_bridge service to your compose.yaml file with ~/phntm_bridge.yaml mounted in the container:
```
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
      - /dev:/dev
    command:
      ros2 launch phntm_bridge bridge_launch.py
```
## Run
```
docker compose up phntm_bridge -d
```

## Install Phantom Bridge in Dev Mode
Clone this repo:
```
cd ~
git clone git@github.com:PhantomCybernetics/phntm_bridge.git phntm_bridge
```
Make the following changes to your docker compose service in compose.yaml. This overwrites /ros2_ws/src/phntm_bridge with live git repo so that you can edit source code from the host filesystem easily:
```
services:
  phntm_bridge:
      - ~/phntm_bridge:/ros2_ws/src/phntm_bridge
    command:
      # launching manually to prevent Docker from exiting on crash
      /bin/sh -c "while sleep 1000; do :; done"
```
### Dev Mode Run:
```
docker compose up phntm_bridge -d
docker exec -it phntm-bridge bash
./scripts/reload-devices.sh # on first run (?!)
ros2 launch phntm_bridge bridge_launch.py
```


