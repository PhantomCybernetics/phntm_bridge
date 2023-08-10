#!/usr/bin/python3

from picamera2.encoders import H264Encoder
from picamera2 import Picamera2
import time
import libcamera

picam2 = Picamera2()
video_config = picam2.create_video_configuration(display=None, transform=libcamera.Transform(hflip=1, vflip=1))
picam2.configure(video_config)
encoder = H264Encoder(bitrate=10000000)
length=25
output = f'test-docker.{length}s-{time.time()}.h264'
picam2.start_recording(encoder, output)
time.sleep(length)
picam2.stop_recording()

print (f'Written output: {output}')

# import av

# container = av.open(format='v4l2', file='/base/soc/i2c0mux/i2c@1/ov5647@36', mode='r')

# import the necessary packages
# from picamera.array import PiRGBArray
# from picamera import PiCamera
# import time
# import cv2
# # initialize the camera and grab a reference to the raw camera capture
# camera = PiCamera()
# rawCapture = PiRGBArray(camera)
# # allow the camera to warmup
# time.sleep(0.1)
# # grab an image from the camera
# camera.capture(rawCapture, format="bgr")
# image = rawCapture.array


# from picamera2.array import PiRGBArray
# from picamera2 import PiCamera2
# import time
# import cv2
# # initialize the camera and grab a reference to the raw camera capture
# camera = PiCamera2()
# print (f'cam : {camera}')
# rawCapture = PiRGBArray(camera)

# # import picamera
# import cv2

# def returnCameraIndexes():
#     # checks the first 10 indexes.
#     index = 0
#     arr = []
#     i = 30
#     while i > 0:
#         cap = cv2.VideoCapture(index)
#         if cap.read()[0]:
#             arr.append(index)
#             cap.release()
#         index += 1
#         i -= 1
#     return arr

# indexes = returnCameraIndexes()

# print (f'cam indexes: {indexes}')

# vid = cv2.VideoCapture(-1)

# print (f'vid: {vid}')

# while(True):

#     # Capture the video frame
#     # by frame
#     ret, frame = vid.read()
#     if frame is not None:
#         print (f'frame: {frame}')


# import CVCamera

# import picamera

# import pyudev

# import libcamera

# context = pyudev.Context()

# for device in context.list_devices():
#     print('dev> '+str(device))

# import av

# container = av.open(file='/dev/media1', mode='r')

# for frame in container.decode(video=1):
#     frame.to_image().save('enctest_frames/frame-%04d.jpg' % frame.index)
