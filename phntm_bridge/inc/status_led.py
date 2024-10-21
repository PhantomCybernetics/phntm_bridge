import rclpy
from rclpy.node import Node, Context
import asyncio
from std_msgs.msg import String, Int32, Bool

# import threading
import time
from typing import Union
from rclpy.qos import QoSProfile
from rclpy.callback_groups import CallbackGroup
from enum import Enum
import gpiod

class StatusLED():

    class Mode(Enum):
        OFF = 0
        ON  = 1
        BLINK_ONCE = 2
        BLINKING   = 3


    def __init__(self, name:str, node:Node, mode:Mode, topic:str=None, qos: Union[QoSProfile, int]=None, pin:int=-1, pin_request=None):
        self.name = name
        self.topic = topic
        self.pin = pin
        self.pin_request = pin_request
        if self.pin > -1:
            self.msg_off = gpiod.line.Value.INACTIVE
            self.msg_on = gpiod.line.Value.ACTIVE
        self.publisher_ = None
        if topic:
            self.publisher_ = node.create_publisher(Bool, self.topic, qos)
            self.msg_off = Bool(data=False)
            self.msg_on = Bool(data=True)

        self.state_ = None
        self.mode_ = mode

        self.on_sec_ = -1.0
        self.interval_sec_ = -1.0
        self.last_on_time_ = -1.0
        self.last_off_time_ = -1.0

        self.blink_task_ = asyncio.get_event_loop().create_task(self.blinker_loop_())


    async def blinker_loop_(self):

        try:
            while self.blink_task_ and not self.blink_task_.cancelled():

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
                
                await asyncio.sleep(.001) # 1ms loop
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        except Exception:
            return
        

    def off(self):
        self.mode_ = StatusLED.Mode.OFF


    def on(self):
        self.mode_ = StatusLED.Mode.ON


    def once(self, on_sec:float=.002):
        self.mode_ = StatusLED.Mode.BLINK_ONCE
        self.on_sec_ = on_sec


    def interval(self, on_sec:float, interval_sec:float):
        if self.mode_ == StatusLED.Mode.BLINKING \
        and abs(self.interval_sec_ -interval_sec) < 0.01:
            return
        
        self.mode_ = StatusLED.Mode.BLINKING

        self.on_sec_ = on_sec
        self.interval_sec_ = interval_sec


    def set_fast_pulse(self):
        self.interval(on_sec=0.01, interval_sec=0.5)


    def send_(self, msg, make_sure:bool=False):
        if self.topic:
            try:
                self.publisher_.publish(msg)
                if make_sure:
                    self.publisher_.publish(msg) # once more to make sure it's delivered

                self.state_ = msg
                if msg.data == True:
                    self.last_on_time_ = time.time()
                elif msg.data == False:
                    self.last_off_time_ = time.time()
            except (Exception, KeyboardInterrupt): # was rclpy._rclpy_pybind11.RCLError
                pass
        
        if self.pin > -1:
            try:
                self.pin_request.set_value(self.pin, msg)
                self.state_ = msg
                if msg == gpiod.line.Value.ACTIVE:
                    self.last_on_time_ = time.time()
                elif msg == gpiod.line.Value.INACTIVE:
                    self.last_off_time_ = time.time()
            except (KeyboardInterrupt): # was rclpy._rclpy_pybind11.RCLError
                pass
            except (Exception) as e: # was rclpy._rclpy_pybind11.RCLError
                print(e);


    def clear(self):
        #self.looping_ = False # kill loop
        try:
            print(f'Clearing led {self.pin}')
            self.blink_task_.cancel()
            self.send_(self.msg_off, True) # off now
            
            if self.publisher_:
                self.publisher_.destroy()
        except Exception as e:
            print(f'Error clearing led {e}')

