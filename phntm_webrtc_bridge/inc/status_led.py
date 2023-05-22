import rclpy
from rclpy.node import Node, Context
import asyncio
from std_msgs.msg import String, Int32, Bool

# import threading
import time
from typing import Union
from rclpy.qos import QoSProfile
from enum import Enum

class StatusLED(Node):

    class Mode(Enum):
        OFF = 0
        ON  = 1
        BLINK_ONCE = 2
        BLINKING   = 3

    def __init__(self, name:str, mode:Mode, topic:str, qos: Union[QoSProfile, int]):
        super().__init__('webrtc_bridge_led_blinker_'+name)
        self.name = name
        self.topic = topic
        self.publisher_ = self.create_publisher(Bool, self.topic, qos)

        self.msg_off = Bool()
        self.msg_off.data = False
        self.msg_on = Bool()
        self.msg_on.data = True

        self.state_:Bool = None
        self.mode_ = mode

        self.on_sec_ = -1.0
        self.interval_sec_ = -1.0
        self.last_on_time_ = -1.0
        self.last_off_time_ = -1.0

        # self.blinked_once_ = False
        self.blink_task_ = asyncio.get_event_loop().create_task(self.blinker_loop_())

    async def blinker_loop_(self):

        #print(">>> ... _blinker starting")

        while True:

            if self.mode_ == StatusLED.Mode.ON:

                if self.state_ != self.msg_on or time.time() > (self.last_on_time_ + 1.0):
                    self.send_(self.msg_on, True)

            elif self.mode_ == StatusLED.Mode.OFF:

                if self.state_ != self.msg_off or time.time() > (self.last_off_time_ + 1.0):
                    self.send_(self.msg_off, True)

            elif self.mode_ == StatusLED.Mode.BLINK_ONCE:

                if self.state_ != self.msg_on:
                    self.send_(self.msg_on)
                elif self.state_ == self.msg_on and time.time() > (self.last_on_time_ + self.on_sec_):
                    self.mode_ = StatusLED.Mode.OFF

            elif self.mode_ == StatusLED.Mode.BLINKING:

                if self.state_ != self.msg_on:
                    if self.last_off_time_ < 0.0 or time.time() > (self.last_off_time_ + self.interval_sec_ - self.on_sec_):
                        self.send_(self.msg_on)
                elif self.state_ == self.msg_on and time.time() > (self.last_on_time_ + self.on_sec_):
                    self.send_(self.msg_off)

            # self.state_ = not self.state_
            # self.send_(self.msg_on if self.state_ else self.msg_off)
            await asyncio.sleep(.001) # 1ms loop

        print(">>> ... _blinker stopped")



    def off(self):
        self.mode_ = StatusLED.Mode.OFF

    def on(self):
        #self.on_sec_ = -1 #kill thread
        #self.blink_thread_ = None
        #self.keep_on = True
        #self._on()
        self.mode_ = StatusLED.Mode.ON

    def once(self, on_sec:float=.002):
        #self.keep_on = False
        #self.blink(on_sec=duration_sec, interval_sec=-1)
        self.mode_ = StatusLED.Mode.BLINK_ONCE
        # self.blinked_once_ = False
        self.on_sec_ = on_sec

    def interval(self, on_sec:float, interval_sec:float):

        self.mode_ = StatusLED.Mode.BLINKING

        self.on_sec_ = on_sec
        self.interval_sec_ = interval_sec

    def set_fast_pulse(self):
        self.interval(on_sec=0.01, interval_sec=0.5)

    def send_(self, msg:Bool, make_sure:bool=False):
        try:
            self.publisher_.publish(msg)
            if make_sure:
                self.publisher_.publish(msg) # once more to make sure it's delivered

            self.state_ = msg
            if msg.data == True:
                self.last_on_time_ = time.time()
            elif msg.data == False:
                self.last_off_time_ = time.time()

        except rclpy._rclpy_pybind11.RCLError:
            pass

    def clear(self):
        #self.looping_ = False # kill loop
        self.blink_task_.cancel()
        self.send_(self.msg_off, True) # off now
        self.publisher_.destroy()

