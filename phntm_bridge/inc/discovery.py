import asyncio
from rclpy.timer import Timer
from  rclpy.impl.rcutils_logger import RcutilsLogger
from rclpy.callback_groups import CallbackGroup
from rclpy.context import Context
from rclpy.node import Node, Parameter, QoSProfile, Publisher
from picamera2 import Picamera2
from .camera import get_camera_info, picam2_has_camera
import socketio
from termcolor import colored as c
import time
import docker
import json

class Discovery:

    def __init__(self, period:float, stop_after:float, node:Node, cbg:CallbackGroup, picam2:Picamera2, docker_client:docker.DockerClient,  sio:socketio.AsyncClient):
        self.period:float = period
        self.stop_after:float = stop_after

        self.node:Node = node
        self.logger:RcutilsLogger = node.get_logger()

        self.callback_group:CallbackGroup = cbg

        self.picam2:Picamera2 = picam2
        self.docker_client = docker_client

        self.discovered_topics:dict[str: dict['msg_types':list[str]]] =  {}
        self.discovered_services:dict[str: dict['msg_types':list[str]]] = {}
        self.discovered_cameras:dict[str: any] = {}
        self.discovered_docker_containers:dict[str: [ docker.models.containers.Container, str ]] = {}

        self.sio:socketio.AsyncClient = sio
        self.timer:Timer = None

    async def start(self):
        await self.stop()

        asyncio.get_event_loop().create_task(self.run_discovery())  # first run now
        self.started_time = time.time()

        if self.period > 0:
            self.logger.info(c(f"Discovering every {self.period}s ...", 'dark_grey'))
            self.timer = self.node.create_timer(self.period, self.run_discovery, self.callback_group) #then every n ses
        else:
            self.logger.info(c(f"Auto discovery is off", 'dark_grey'))

        await self.report_discovery()

    async def stop(self):
        if self.timer is not None:
            self.logger.info(c(f'Discovery stopped after {self.stop_after}s', 'dark_grey'))
            self.timer.destroy()
            self.timer = None
            await self.report_discovery()

    def running(self) -> bool:
        if self.timer is not None:
            return True
        return False

    # spinned by timer
    async def run_discovery(self):

        self.logger.info(c(f'Discovering things...', 'dark_grey'))

        #topics
        topics_changed = False
        new_topics = self.node.get_topic_names_and_types()

        for topic_info in new_topics:
            topic = topic_info[0]
            # TODO: blacklist topics
            if not topic in self.discovered_topics:
                self.logger.info(c(f'Discovered topic {topic}', 'light_blue'))
                self.discovered_topics[topic] = { 'msg_types': topic_info[1] }
                topics_changed = True
        if topics_changed:
            await self.report_topics()

        #services
        services_changed = False
        new_services = self.node.get_service_names_and_types()

        for service_info in new_services:
            service = service_info[0]
            # TODO: blacklist services
            if not service in self.discovered_services:
                self.discovered_services[service] = { 'msg_types': service_info[1] }
                services_changed = True
                self.logger.info(c(f'Discovered service {service}', 'magenta'))
        if services_changed:
            await self.report_services()

        #cameras
        cameras_changed = False
        new_cameras = []

        if self.picam2 is not None:
            new_cameras = get_camera_info(self.picam2)
            for [ id_cam, cam_info ] in new_cameras:
                # print (f'cam "{id_cam}" <<{cam_info}>>')
                # id_cam = cam_info[0]
                # TODO: blacklist cameras
                if not id_cam in self.discovered_cameras.keys():
                    self.discovered_cameras[id_cam] = cam_info
                    cameras_changed = True
                    self.logger.info(c(f'Discovered camera {id_cam} model {cam_info["Model"]}', 'green'))
        if cameras_changed:
            await self.report_cameras()

        #docker
        docker_containers_changed = False
        new_docker_containers = []
        if self.docker_client:
            new_docker_containers = self.docker_client.containers.list(all=True)
        for container in new_docker_containers:
            # print (f'container "{container.id}" <<{container.name}>>')
            # id_cam = cam_info[0]
            # TODO: blacklist conainers?
            if not container.id in self.discovered_docker_containers.keys():
                self.discovered_docker_containers[container.id] = [ container, container.status ]
                docker_containers_changed = True
                self.logger.info(c(f'Discovered Docker container {container.name} aka {container.short_id} {container.status}', 'dark_grey'))
            elif self.discovered_docker_containers[container.id][1] != container.status:
                self.discovered_docker_containers[container.id][0] = container
                self.discovered_docker_containers[container.id][1] = f'{container.status}' #copy
                docker_containers_changed = True
                self.logger.info(c(f'Docker container {container.name} aka {container.short_id} changed status to {container.status}', 'dark_grey'))
        if docker_containers_changed:
            await self.report_docker()

        if self.stop_after > 0.0 and self.started_time+self.stop_after < time.time():
            await self.stop()

    async def report_topics(self):
        if not self.sio or not self.sio.connected:
            return

        data = []
        for topic in self.discovered_topics.keys():
            topic_data = [ topic ] # msg types follow
            for msg_type in self.discovered_topics[topic]['msg_types']:
                topic_data.append(msg_type)
            data.append(topic_data)

        self.logger.info(f'Reporting {len(data)} topics')

        await self.sio.emit(
            event='topics',
            data=data,
            callback=None
            )


    async def report_services(self):
        if not self.sio or not self.sio.connected:
            return

        data = []
        for service in self.discovered_services.keys():
            service_data = [ service ]  # msg types follow
            for msg_type in self.discovered_services[service]['msg_types']:
                service_data.append(msg_type)
            data.append(service_data)

        self.logger.info(f'Reporting {len(data)} services')

        await self.sio.emit(
            event='services',
            data=data,
            callback=None
            )


    async def report_cameras(self):
        if not self.sio or not self.sio.connected:
            return

        data = []
        for id_cam in self.discovered_cameras.keys():
            data.append( [ id_cam,  self.discovered_cameras[id_cam] ])

        self.logger.info(f'Reporting {len(data)} cameras')

        await self.sio.emit(
            event='cameras',
            data=data,
            callback=None
            )

    async def report_docker(self):
        if not self.sio or not self.sio.connected:
            return

        data = []
        for id_container in self.discovered_docker_containers.keys():
            cont = self.discovered_docker_containers[id_container][0]
            cont_data = {
                'id': id_container,
                'name': cont.name,
                'image': cont.image.id,
                'short_id': cont.short_id,
                'status': cont.status
            }
            data.append(cont_data)

        self.logger.info(f'Reporting {len(data)} docker containers')

        await self.sio.emit(
            event='docker',
            data=data,
            callback=None
            )

    async def report_discovery(self):
        if not self.sio or not self.sio.connected:
            return

        data = True if self.timer is not None else False

        self.logger.info(f'Reporting discovery running: {data}')

        await self.sio.emit(
            event='discovery',
            data=data,
            callback=None
            )