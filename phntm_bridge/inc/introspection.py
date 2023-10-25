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
from .peer import WRTCPeer

from pyee.base import EventEmitter
from pyee.asyncio import AsyncIOEventEmitter

class Introspection (AsyncIOEventEmitter):

    def __init__(self, period:float, stop_after:float, ctrl_node:Node, picam2:Picamera2, docker_client:docker.DockerClient, sio:socketio.AsyncClient):
        super().__init__()
        self.period:float = period
        self.stop_after:float = stop_after

        self.ctrl_node:Node = ctrl_node
        self.logger = ctrl_node.get_logger()

        self.picam2:Picamera2 = picam2
        self.docker_client = docker_client

        self.discovered_topics:dict[str: dict['msg_types':list[str]]] =  {}
        self.discovered_services:dict[str: dict['msg_types':list[str]]] = {}
        self.discovered_cameras:dict[str: any] = {}
        self.discovered_docker_containers:dict[str: [ docker.models.containers.Container, str ]] = {}
        self.discovered_nodes:dict[str: dict[
            'namespace':str,
            'publishers':dict[str:list[str]],
            'subscribers':dict[str:list[str]],
            'services':dict[str:list[str]]
        ]] =  {}

        self.sio:socketio.AsyncClient = sio
        self.running:bool = False

        self.waiting_peers:list = []

    def add_waiting_peer(self, peer:WRTCPeer):
        if not peer in self.waiting_peers:
            self.waiting_peers.append(peer)

    def remove_waiting_peer(self, peer:WRTCPeer):
        if peer in self.waiting_peers:
            self.waiting_peers.remove(peer)

    async def start(self):

        self.started_time = time.time()

        if self.running:
            return False # only renew timer

        self.running = True
        self.logger.info(c(f'Introspection started', 'dark_grey'))
        await self.report_introspection()

        while self.running:
            new_discoveries = await self.run_discovery() # True if cameras or topics discovered

            if new_discoveries:
                for peer in self.waiting_peers.copy():
                    self.waiting_peers.remove(peer)
                    await self.ctrl_node.process_peer_subscriptions(peer, send_update=True)

            something_missing = len(self.waiting_peers) > 0
            await asyncio.sleep(self.period)

            if not something_missing: #keep discovering until all is found (TODO or user hits stop)
                if self.period <= 0: # one shot
                    await self.stop(report=True)
                    return
                if self.stop_after > 0.0 and self.started_time+self.stop_after < time.time():
                    self.logger.info(c(f'Introspection stopped automatically', 'dark_grey'))
                    await self.stop(report=True)
                    return

    async def stop(self, report:bool = True):
        if self.running:
            self.logger.info(c(f'Introspection stopped', 'dark_grey'))
            self.running = False
            self.waiting_peers = []
            if report:
                await self.report_introspection()


    # spinned by timer
    async def run_discovery(self) -> bool:

        wating_for = set()
        for peer in self.waiting_peers:
            for topic in peer.topics_not_discovered:
                wating_for.add(topic)
            for cam in peer.cameras_not_discovered:
                wating_for.add(cam)

        self.logger.info(c(f'Introspecting... ({len(self.waiting_peers)} peers waiting{(" for " + ", ".join(set(wating_for))) if len(wating_for) > 0 else ""})', 'dark_grey'))

        nodes_changed = False
        new_nodes = self.ctrl_node.get_node_names_and_namespaces()

        for node_info in new_nodes:
            node = node_info[0]
            namespace = node_info[1]
            if not node in self.discovered_nodes.keys():
                self.logger.info(c(f'Discovered node {node} ns={namespace}', 'light_green'))
                self.discovered_nodes[node] = {
                    'namespace': namespace
                }
                nodes_changed = True

            publishers = self.ctrl_node.get_publisher_names_and_types_by_node(node, namespace)
            for publisher in publishers:
                topic = publisher[0]
                msg_types = publisher[1]
                if not 'publishers' in self.discovered_nodes[node]:
                    self.discovered_nodes[node]['publishers'] = {}
                if not topic in self.discovered_nodes[node]['publishers'].keys() \
                    or self.discovered_nodes[node]['publishers'][topic] != msg_types:
                    self.discovered_nodes[node]['publishers'][topic] = msg_types
                    self.logger.info(c(f'{node} > {topic} {msg_types}', 'light_green'))
                    nodes_changed = True

            new_subscribers = self.ctrl_node.get_subscriber_names_and_types_by_node(node, namespace)
            for subscriber in new_subscribers:
                topic = subscriber[0]
                msg_types = subscriber[1]
                if not 'subscribers' in self.discovered_nodes[node]:
                    self.discovered_nodes[node]['subscribers'] = {}
                if not topic in self.discovered_nodes[node]['subscribers'].keys() \
                    or self.discovered_nodes[node]['subscribers'][topic] != msg_types:
                    self.discovered_nodes[node]['subscribers'][topic] = msg_types
                    self.logger.info(c(f'{node} < {topic} {msg_types}', 'light_green'))
                    nodes_changed = True

            new_services = self.ctrl_node.get_service_names_and_types_by_node(node, namespace)
            for service_info in new_services:
                id_service = service_info[0]
                msg_types = service_info[1]
                if not 'services' in self.discovered_nodes[node]:
                    self.discovered_nodes[node]['services'] = {}
                if not id_service in self.discovered_nodes[node]['services'].keys() \
                    or self.discovered_nodes[node]['services'][id_service] != msg_types:
                    self.discovered_nodes[node]['services'][id_service] = msg_types
                    self.logger.info(c(f'{node} <> {id_service} {msg_types}', 'light_green'))
                    nodes_changed = True

        if nodes_changed:
            await self.report_nodes()

        #topics
        topics_changed = False
        new_topics = self.ctrl_node.get_topic_names_and_types()

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
        new_services = self.ctrl_node.get_service_names_and_types()

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
                self.logger.info(c(f'Discovered Docker container {container.name} aka {container.short_id} {container.status}', 'blue'))
            elif self.discovered_docker_containers[container.id][1] != container.status:
                self.discovered_docker_containers[container.id][0] = container
                self.discovered_docker_containers[container.id][1] = f'{container.status}' #copy
                docker_containers_changed = True
                self.logger.info(c(f'Docker container {container.name} aka {container.short_id} changed status to {container.status}', 'blue'))
        if docker_containers_changed:
            await self.report_docker()

        return topics_changed or cameras_changed

    def get_nodes_data(self):
        return self.discovered_nodes

    async def report_nodes(self):

        self.emit('nodes', self.discovered_nodes.keys())

        if not self.sio or not self.sio.connected:
            return

        data = self.get_nodes_data()
        self.logger.info(c(f'Reporting {len(data)} nodes', 'dark_grey'))

        await self.sio.emit(
            event='nodes',
            data=data,
            callback=None
            )

    def get_topics_data(self):
        data = []
        for topic in self.discovered_topics.keys():
            topic_data = [ topic ] # msg types follow
            for msg_type in self.discovered_topics[topic]['msg_types']:
                topic_data.append(msg_type)
            data.append(topic_data)
        return data

    async def report_topics(self):

        self.emit('topics', self.discovered_topics.keys())

        if not self.sio or not self.sio.connected:
            return

        data = self.get_topics_data()
        self.logger.info(c(f'Reporting {len(data)} topics', 'dark_grey'))

        await self.sio.emit(
            event='topics',
            data=data,
            callback=None
            )

    def get_services_data(self):
        data = []
        for service in self.discovered_services.keys():
            service_data = [ service ]  # msg types follow
            for msg_type in self.discovered_services[service]['msg_types']:
                service_data.append(msg_type)
            data.append(service_data)
        return data

    async def report_services(self):

        self.emit('services', self.discovered_services.keys())

        if not self.sio or not self.sio.connected:
            return

        data = self.get_services_data()
        self.logger.info(c(f'Reporting {len(data)} services', 'dark_grey'))

        await self.sio.emit(
            event='services',
            data=data,
            callback=None
            )

    def get_cameras_data(self):
        data = {}
        for id_cam in self.discovered_cameras.keys():
            data[id_cam] = self.discovered_cameras[id_cam]
        return data

    async def report_cameras(self):

        self.emit('cameras', self.discovered_cameras.keys())

        if not self.sio or not self.sio.connected:
            return

        data = self.get_cameras_data()
        self.logger.info(c(f'Reporting {len(data)} cameras', 'dark_grey'))

        await self.sio.emit(
            event='cameras',
            data=data,
            callback=None
            )

    def get_docker_data(self):
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
        return data

    async def report_docker(self):

        self.emit('docker', self.discovered_docker_containers.keys())

        if not self.sio or not self.sio.connected:
            return

        data = self.get_docker_data()
        self.logger.info(c(f'Reporting {len(data)} docker containers', 'dark_grey'))

        await self.sio.emit(
            event='docker',
            data=data,
            callback=None
            )

    async def report_introspection(self):

        self.emit('introspection', self.running)

        if not self.sio or not self.sio.connected:
            return

        self.logger.info(c(f'Reporting introspection {"running" if self.running else "stopped"}', 'dark_grey'))

        await self.sio.emit(
            event='introspection',
            data=self.running,
            callback=None
            )