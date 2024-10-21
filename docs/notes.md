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