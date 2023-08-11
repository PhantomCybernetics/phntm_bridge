
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput
from picamera2 import Picamera2

def get_camera_info(picam2:Picamera2):
    data = []
    info = picam2.global_camera_info()

    for c in info:
        print (str(c))
        cam_data = [
            f'picam2{c["Id"]}', #our id
            c,
            # msg types follow
        ]
        data.append(cam_data)
    return data
