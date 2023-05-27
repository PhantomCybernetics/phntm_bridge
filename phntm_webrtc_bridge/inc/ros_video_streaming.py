
from aiortc.contrib.media import MediaStreamTrack
from av.frame import Frame
from av.video.frame import VideoFrame

class ROSVideoStreamTrack(MediaStreamTrack):

    kind = "video"

    async def recv(self) -> VideoFrame:
        print(' >> returning video frame')
        f = VideoFrame()
        return f