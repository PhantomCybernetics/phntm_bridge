#!/usr/bin/python3

from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput
from picamera2 import Picamera2
import time
import libcamera
import io
import av
from termcolor import colored

picam2 = Picamera2()
print (colored(f'Picamera2 info: {picam2.global_camera_info()}', 'yellow'))
video_config = picam2.create_preview_configuration(display='main',
                                                 encode='main',
                                                 transform=libcamera.Transform(hflip=1, vflip=1),
                                                 queue=True
                                                 )
picam2.configure(video_config)
encoder = H264Encoder(bitrate=10000000)
length=25
# output = f'test-docker.{length}s-{time.time()}.h264'

class PacketsOutput(FileOutput):
    def outputframe(self, frame, keyframe=True, timestamp=None):
        """Outputs frame from encoder

        :param frame: Frame
        :type frame: bytes
        :param keyframe: Whether frame is a keyframe, defaults to True
        :type keyframe: bool, optional
        :param timestamp: Timestamp of frame
        :type timestamp: int
        """
        if self.recording:
            if self._firstframe:
                if not keyframe:
                    return
                else:
                    self._firstframe = False
            # pass
            print(f'Got frame {timestamp}: {len(frame)} B{" KEYFRAME" if keyframe else ""}')
            # self._write(frame, timestamp)

# buffer = PiCameraH264Buffer()
output = PacketsOutput()

# pts_buffer = io.BytesIO()
# pts_output = FileOutput(pts_buffer)

time.sleep(2)

print (f'Picam2 ready')

# picam2.start_recording()
picam2.start_encoder(encoder=encoder, output=output)
picam2.start()

print (f'Picam2 running (Ctr-C to stop)')

while True:
    time.sleep(10)
    pass

# try:
#     frame = bytes(65507)
#     keyframe = True
#     while True:
#         array = encoder.capture_array('main')
#         # output.outputframe(frame, keyframe=keyframe)
#         print(f'captured {len(array)}x{len(array[0])} array')
#         # print (f'Buffer has {len(buffer.getbuffer())} B{" [REC]" if output.recording else ""}{" [DEAD]" if output.dead else ""}')
#         # output.outputframe(frame, keyframe=keyframe)
#         # buffer.flush()
#         # picam2.capture_buffers()
#         time.sleep(1)

# except Exception as e:
#     print (f'Exception: {e}')
#     pass

# picam2.start()
# time.sleep(length)
picam2.stop_recording()

# print (f'Written output: {output}')

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
