import asyncio
from rclpy.timer import Timer
from  rclpy.impl.rcutils_logger import RcutilsLogger
from rclpy.callback_groups import CallbackGroup
from rclpy.context import Context
from rclpy.node import Node, Parameter, QoSProfile, Publisher
from picamera2 import Picamera2
from .camera import get_camera_info, picam2_has_camera
import socketio

class Discovery:

    node:Node
    callback_group: CallbackGroup

    timer_:Timer
    period:float
    logger:RcutilsLogger
    discovered_topics_:dict[str: dict['msg_types':list[str]]]
    discovered_services_:dict[str: dict['msg_types':list[str]]]
    discovered_cameras_:dict[str: any]

    picam2:Picamera2
    sio:socketio.AsyncClient

    def __init__(self, period:float, node:Node, cbg:CallbackGroup, picam2:Picamera2, sio:socketio.AsyncClient):
        self.period = period

        self.node = node
        self.logger = node.get_logger()

        self.callback_group = cbg

        self.picam2 = picam2

        self.discovered_topics_ =  {}
        self.discovered_services_= {}
        self.discovered_cameras_ = {}

        self.sio = sio

    def start(self):
        self.logger.info(f"Discovering every {self.period}s ...")
        asyncio.get_event_loop().create_task(self.run_discovery())  # first run now
        self.timer_ = self.node.create_timer(self.period, self.run_discovery, self.callback_group) #then every n ses

    def stop(self):
        # TODO
        pass

    # spinned by timer
    async def run_discovery(self):

        self.logger.info(f'Discovering things...')

        #topics
        topics_changed = False
        new_topics = self.node.get_topic_names_and_types()

        for topic_info in new_topics:
            topic = topic_info[0]
            # TODO: blacklist topics
            if not topic in self.discovered_topics_:
                self.logger.debug(f'Discovered topic {topic}')
                self.discovered_topics_[topic] = { 'msg_types': topic_info[1] }
                topics_changed = True
        if topics_changed:
            await self.report_topics()

        #services
        services_changed = False
        new_services = self.node.get_service_names_and_types()

        for service_info in new_services:
            service = service_info[0]
            # TODO: blacklist services
            if not service in self.discovered_services_:
                self.discovered_services_[service] = { 'msg_types': service_info[1] }
                services_changed = True
                self.logger.debug(f'Discovered service {service}')
        if services_changed:
            await self.report_services()

        #cameras
        cameras_changed = False
        new_cameras = []
        if self.picam2 is not None:
            new_cameras = get_camera_info(self.picam2)
            for cam_info in new_cameras:
                cam = cam_info.Id
                # TODO: blacklist cameras
                if not cam in self.discovered_cameras_:
                    self.discovered_cameras_[cam] = cam_info
                    cameras_changed = True
                    self.logger.debug(f'Discovered cameea {cam} {cam_info.Model}')
        if cameras_changed:
            await self.report_cameras()


    async def report_topics(self):
        if not self.sio or not self.sio.connected:
            self.logger.error(f'Not reporting topics, {self.sio} connected={self.sio.connected}')
            return

        data = []
        for topic in self.discovered_topics_.keys():
            topic_data = [ topic ] # msg types follow
            for msg_type in self.discovered_topics_[topic]['msg_types']:
                topic_data.append(msg_type)
            data.append(topic_data)

        self.logger.warn(f'Reporting topics {str(data)}')

        await self.sio.emit(
            event='topics',
            data=data,
            callback=None
            )


    async def report_services(self):
        if not self.sio or not self.sio.connected:
            return

        data = []
        for service in self.discovered_services_.keys():
            service_data = [ service ]  # msg types follow
            for msg_type in self.discovered_services_[service]['msg_types']:
                service_data.append(msg_type)
            data.append(service_data)

        # self.logger.warn(f'Reporting services {str(data)}')

        await self.sio.emit(
            event='services',
            data=data,
            callback=None
            )


    async def report_cameras(self):
        if not self.sio or not self.sio.connected:
            return

        data = []
        for cam in self.discovered_cameras_.keys():
            data.append(self.discovered_cameras_[cam])

        # self.logger.warn(f'Reporting cameras {str(data)}')

        await self.sio.emit(
            event='cameras',
            data=data,
            callback=None
            )