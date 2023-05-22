import rclpy
from rclpy.node import Node, Context
import asyncio
from std_msgs.msg import String, Int32, Bool

# import threading
import time
from typing import Union
from rclpy.qos import QoSProfile

class StatusLED(Node):
    def __init__(self, name:str, topic:str, qos: Union[QoSProfile, int]):
        super().__init__('webrtc_bridge_led_blinker_'+name)
        self.name = name
        self.topic = topic
        self.publisher_ = self.create_publisher(Bool, self.topic, qos)

        self.msg_off = Bool()
        self.msg_off.data = False
        self.msg_on = Bool()
        self.msg_on.data = True

        self.state_ = False
        #self.looping_ = True
        #print(f">>> {self.name}.blinker_loop_ starting ...")

        self.blink_task_ = asyncio.get_event_loop().create_task(self.blinker_loop_())

    async def blinker_loop_(self):

        #print(">>> ... _blinker starting")

        while True:
            self.state_ = not self.state_
            self.send_(self.msg_on if self.state_ else self.msg_off)
            await asyncio.sleep(.1)

        print(">>> ... _blinker stopped")

    def set_fast_pulse(self):
        #self.keep_on = False
        #self.blink(on_sec=0.01, interval_sec=0.5)
        pass

    def off(self):
        pass

    def on(self):
        #self.on_sec_ = -1 #kill thread
        #self.blink_thread_ = None
        #self.keep_on = True
        #self._on()
        pass

    def blink(self, duration_sec:float=.00001):
        #self.keep_on = False
        #self.blink(on_sec=duration_sec, interval_sec=-1)
        pass

    def blink_every(self, on_sec:float, interval_sec:float):

        # if self.on_timer_ != None:
        #    self.destroy_timer(self.on_timer_)
        #    self.on_timer_ = None

        # self.on_sec_ = on_sec
        # self.interval_sec_ = interval_sec

        # if self.blink_thread_ == None or not self.blink_thread_.is_alive:
        #     self.blink_thread_ = threading.Thread(target=self._blinker)
        #     self.blink_thread_.start()

        #self.c_ = asyncio.run(self._do_blink())
        #self.on_timer_ = self.create_timer(interval_sec, lambda: self._on(duration_sec=on_sec))
        pass

    def send_(self, msg:Bool, make_sure:bool=False):
        try:
            self.publisher_.publish(msg)
            if make_sure:
                self.send_(msg, False) # once more to make sure it's delivered

        except rclpy._rclpy_pybind11.RCLError:
            pass

    def clear(self):
        #self.looping_ = False # kill loop
        self.blink_task_.cancel()
        self.send_(self.msg_off, True) # off now
        self.publisher_.destroy()

