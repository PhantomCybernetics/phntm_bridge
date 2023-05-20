import rclpy
from rclpy.node import Node, Context
import asyncio
from std_msgs.msg import String, Int32, Bool

import threading
import time

class StatusLED(Node):
    def __init__(self, name:str, topic:str):
        super().__init__('webrtc_bridge_led_blinker_'+name)
        self.topic = topic
        self.publisher_ = self.create_publisher(Bool, self.topic, 3)

        self.msg_off = Bool()
        self.msg_off.data = False
        self.msg_on = Bool()
        self.msg_on.data = True

        self.blink_thread_ = None
        # self.on_timer_ = None
        self.keep_on = False

    def set_disconnected(self):
        self.keep_on = False
        self.blink(on_sec=0.01, interval_sec=0.5)

    def set_connected(self):
        self.on_sec_ = -1 #kill thread
        self.blink_thread_ = None
        self.keep_on = True
        self._on()

    def blink(self, on_sec:float, interval_sec:float):

        # if self.on_timer_ != None:
        #    self.destroy_timer(self.on_timer_)
        #    self.on_timer_ = None

        self.on_sec_ = on_sec
        self.interval_sec_ = interval_sec

        if self.blink_thread_ == None or not self.blink_thread_.is_alive:
            self.blink_thread_ = threading.Thread(target=self._blinker)
            self.blink_thread_.start()

        #self.c_ = asyncio.run(self._do_blink())
        #self.on_timer_ = self.create_timer(interval_sec, lambda: self._on(duration_sec=on_sec))

    def _blinker(self):
        # print(">>> _blinker starting ...")
        while self.on_sec_ > 0:
            on_sec = self.on_sec_
            self._on()
            if self.interval_sec_ < 0:
                self.on_sec_ = -1

            if self.keep_on:
                return

            time.sleep(on_sec)

            self._off()

            if self.on_sec_ > 0:
                if self.interval_sec_ > 0:
                    time.sleep(self.interval_sec_-self.on_sec_)

        self.blink_thread_ = None
        # print(">>> ... _blinker stopped")

    def _on(self):
        # print('led on')
        try:
            self.publisher_.publish(self.msg_on)
        except rclpy._rclpy_pybind11.RCLError:
            pass

    def _off(self):
        # print('led off')
        self.keep_on = False
        try:
            self.publisher_.publish(self.msg_off)
        except rclpy._rclpy_pybind11.RCLError:
            pass

    def stop(self):
        self.on_sec_ = -1 # kill loop
        self.keep_on = False
        self.blink_thread_ = None
        self._off() # off now

    def once(self, duration_sec:float=.00001):
        self.keep_on = False
        self.blink(on_sec=duration_sec, interval_sec=-1)

