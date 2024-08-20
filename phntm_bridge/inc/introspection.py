import asyncio
from rclpy.timer import Timer
from  rclpy.impl.rcutils_logger import RcutilsLogger
from rclpy.callback_groups import CallbackGroup
from rclpy.context import Context
from rclpy.node import Node, Parameter, QoSProfile, Publisher
from rosidl_runtime_py.utilities import get_message, get_interface
from rosidl_runtime_py import get_interface_path
import os

try:
    from .camera import get_camera_info
except ModuleNotFoundError:
    pass
import socketio
from termcolor import colored as c
import time
import docker
import json
from .peer import WRTCPeer

from pyee.base import EventEmitter
from pyee.asyncio import AsyncIOEventEmitter

class Introspection (AsyncIOEventEmitter):

    def __init__(self, period:float, stop_after:float, ctrl_node:Node, docker_client:docker.DockerClient, sio:socketio.AsyncClient):
        super().__init__()
        self.period:float = period
        self.stop_after:float = stop_after

        self.ctrl_node:Node = ctrl_node
        self.logger = ctrl_node.get_logger()

        self.picam2 = ctrl_node.picam2
        self.docker_client = docker_client

        self.discovered_topics:dict[str: dict['msg_type':str]] =  {}
        self.discovered_idls:dict[str:str] = {}
        self.discovered_services:dict[str: dict['msg_type':str]] = {}
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
            new_discoveries:bool = await self.run_discovery() # True if cameras or topics discovered

            for peer in self.waiting_peers.copy():
                update_peer = new_discoveries
                if not update_peer:
                    for topic in peer.topics_not_discovered:
                        if topic in self.discovered_topics.keys():
                            update_peer = True
                            break
                if not update_peer:
                    for cam in peer.cameras_not_discovered:
                        if cam in self.discovered_cameras.keys():
                            update_peer = True
                            break
                if update_peer:
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

    async def collect_idls(self, msg_type) -> bool:
        if msg_type in self.discovered_idls:
            return False # no change
        
        idl_path = get_interface_path(msg_type)
        idl_path = os.path.splitext(idl_path)[0] + '.idl'
        idl_raw = ''
        try:
            with open(idl_path, 'r') as file:
                idl_raw = file.read()
        except FileNotFoundError:
            self.logger.error(f'IDL not found for {msg_type}, file not found at {idl_path}')
            return False # no change
        
        idl_clear = ''
        first_valid_line = False
        inluded_message_types = []
        for line in idl_raw.splitlines():
            if line.startswith('//'): # skipping comments
                continue
            if line == '' and not first_valid_line:
                continue
            if line != '':
                first_valid_line = True
            idl_clear = idl_clear + line + '\n'

            if line.startswith('#include') and line.find('.idl') > -1: # handle includes
                inc_path = line.replace('#include', '').strip(' "\'')
                inc_msg_type = inc_path.replace('.idl', '');
                inluded_message_types.append(inc_msg_type)
            
        self.discovered_idls[msg_type] = idl_clear
        self.logger.info(c(f'Loaded {idl_path} for [{msg_type}]', 'light_yellow'))
        # print(c(f'{msg_type}: {idl_path}:\n{idl_clear}', 'light_magenta'))

        for inc_msg_type in inluded_message_types:
            if not inc_msg_type in self.discovered_idls:
                # print(c(f'#inc {inc_msg_type} > ', 'light_yellow'))
                await self.collect_idls(inc_msg_type)
    
        return True


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
        new_nodes = await asyncio.get_event_loop().run_in_executor(None, self.ctrl_node.get_node_names_and_namespaces)
        
        for old_node in self.discovered_nodes.copy().keys():
            old_node_found = False
            for node_info in new_nodes:
                if node_info[0] == old_node:
                    old_node_found = True
                    break
            if not old_node_found: # old node disappeared, trigger change
                nodes_changed = True
                del self.discovered_nodes[old_node]
        
        for node_info in new_nodes:
            node = node_info[0]
            namespace = node_info[1]
            if not node in self.discovered_nodes.keys():
                self.logger.info(c(f'Discovered node {node} ns={namespace}', 'light_green'))
                self.discovered_nodes[node] = {
                    'namespace': namespace,
                    'publishers': {},
                    'subscribers': {},
                    'services': {}
                }
                nodes_changed = True

            new_publishers = await asyncio.get_event_loop().run_in_executor(None, self.ctrl_node.get_publisher_names_and_types_by_node, node, namespace)
            
            for old_pub_topic in self.discovered_nodes[node]['publishers'].copy().keys():
                pub_found = False
                for pub_info in new_publishers:
                    if pub_info[0] == old_pub_topic:
                        pub_found = True
                        break
                if not pub_found:
                    nodes_changed = True
                    del self.discovered_nodes[node]['publishers'][old_pub_topic]
                    
            for pub_info in new_publishers:
                topic = pub_info[0]
                msg_type = pub_info[1][0]
                if not topic in self.discovered_nodes[node]['publishers'].keys() \
                or self.discovered_nodes[node]['publishers'][topic] != msg_type:
                    self.discovered_nodes[node]['publishers'][topic] = msg_type
                    self.logger.info(c(f'{node} > {topic} [{msg_type}]', 'light_green'))
                    nodes_changed = True

            new_subscribers = await asyncio.get_event_loop().run_in_executor(None, self.ctrl_node.get_subscriber_names_and_types_by_node, node, namespace)
            
            for old_sub_topic in self.discovered_nodes[node]['subscribers'].copy().keys():
                sub_found = False
                for sub_info in new_subscribers:
                    if sub_info[0] == old_sub_topic:
                        sub_found = True
                        break
                if not sub_found:
                    nodes_changed = True
                    del self.discovered_nodes[node]['subscribers'][old_sub_topic]
            
            for sub_info in new_subscribers:
                topic = sub_info[0]
                msg_type = sub_info[1][0]
                if not topic in self.discovered_nodes[node]['subscribers'].keys() \
                or self.discovered_nodes[node]['subscribers'][topic] != msg_type:
                    self.discovered_nodes[node]['subscribers'][topic] = msg_type
                    self.logger.info(c(f'{node} < {topic} {msg_type}', 'light_green'))
                    nodes_changed = True

            new_services = await asyncio.get_event_loop().run_in_executor(None, self.ctrl_node.get_service_names_and_types_by_node, node, namespace)
            
            for old_serv in self.discovered_nodes[node]['services'].copy().keys():
                serv_found = False
                for serv_info in new_services:
                    if serv_info[0] == old_serv:
                        serv_found = True
                        break
                if not serv_found:
                    nodes_changed = True
                    del self.discovered_nodes[node]['services'][old_serv]
            
            for serv_info in new_services:
                id_service = serv_info[0]
                msg_type = serv_info[1][0]
                if not id_service in self.discovered_nodes[node]['services'].keys() \
                or self.discovered_nodes[node]['services'][id_service] != msg_type:
                    self.discovered_nodes[node]['services'][id_service] = msg_type
                    if len(serv_info[1]) > 1:
                        self.logger.info(c(f'srv {id_service} @ {node} [{msg_type}] ?? {str(serv_info[1])}', 'red'))
                    else:
                        self.logger.info(c(f'srv {id_service} @ {node} [{msg_type}]', 'light_green'))
                    nodes_changed = True

        idls_changed = False

        # topics + extract message type idls
        topics_changed = False
        new_topics = self.ctrl_node.get_topic_names_and_types()
        
        for topic_info in new_topics:
            topic = topic_info[0]
            # TODO: blacklist topics
            if not topic in self.discovered_topics:
                msg_type = topic_info[1][0]
                self.discovered_topics[topic] = { 'msg_type': msg_type }
                topics_changed = True
                self.logger.info(c(f'Discovered topic {topic} [{msg_type}]', 'light_blue'))
                idls_changed = await self.collect_idls(msg_type) or idls_changed

        # services + extract message type idls
        services_changed = False
        new_services = self.ctrl_node.get_service_names_and_types()

        for service_info in new_services:
            service = service_info[0]
            # TODO: blacklist services
            if not service in self.discovered_services:
                msg_type = service_info[1][0]
                self.discovered_services[service] = { 'msg_type': msg_type }
                services_changed = True
                self.logger.info(c(f'Discovered service {service} [{msg_type}]', 'magenta'))
                idls_changed = await self.collect_idls(msg_type) or idls_changed
        
        if not 'std_msgs/msg/Byte' in self.discovered_idls: # force add byte as we use on the client to send heartbeat (but not producing into a topic => no auto type discovery)
            idls_changed = await self.collect_idls('std_msgs/msg/Byte') or idls_changed
        
        if idls_changed:
            await self.report_idls()
        if nodes_changed:
            await self.report_nodes()
        if topics_changed:
            await self.report_topics()
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

        try:
            data = self.get_nodes_data()
            self.logger.info(c(f'Reporting {len(data)} nodes', 'dark_grey'))

            await self.sio.emit(
                event='nodes',
                data=data,
                callback=None
                )
        except:
            pass

    def get_idls_data(self):
        return self.discovered_idls

    async def report_idls(self):

        if not self.sio or not self.sio.connected:
            self.logger.error(f'Not reporting IDLS, socket not ready')
            return

        try:
            data = self.get_idls_data()
            self.logger.info(c(f'Reporting {len(data)} idls', 'dark_grey'))

            await self.sio.emit(
                event='idls',
                data=data,
                callback=None
                )
        except Exception as e:
            self.logger.error(f'Exception while reporting idls: {str(e)}')
            pass

    def get_topics_data(self):
        data = []
        for topic in self.discovered_topics.keys():
            topic_data = [
                topic,
                self.discovered_topics[topic]['msg_type']
                ]
            data.append(topic_data)
        return data

    async def report_topics(self):

        self.emit('topics', self.discovered_topics.keys())

        if not self.sio or not self.sio.connected:
            return

        try:
            data = self.get_topics_data()
            self.logger.info(c(f'Reporting {len(data)} topics', 'dark_grey'))

            await self.sio.emit(
                event='topics',
                data=data,
                callback=None
                )
        except:
            pass

    def get_services_data(self):
        data = []
        for service in self.discovered_services.keys():
            service_data = [
                service,
                self.discovered_services[service]['msg_type']
                ]
            data.append(service_data)
        return data

    async def report_services(self):

        self.emit('services', self.discovered_services.keys())

        if not self.sio or not self.sio.connected:
            return

        try:
            data = self.get_services_data()
            self.logger.info(c(f'Reporting {len(data)} services', 'dark_grey'))

            await self.sio.emit(
                event='services',
                data=data,
                callback=None
                )
        except:
            pass

    def get_cameras_data(self):
        data = {}
        for id_cam in self.discovered_cameras.keys():
            data[id_cam] = self.discovered_cameras[id_cam]
        return data

    async def report_cameras(self):

        self.emit('cameras', self.discovered_cameras.keys())

        if not self.sio or not self.sio.connected:
            return

        try:
            data = self.get_cameras_data()
            self.logger.info(c(f'Reporting {len(data)} cameras', 'dark_grey'))

            await self.sio.emit(
                event='cameras',
                data=data,
                callback=None
                )
        except:
            pass

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

        try:
            data = self.get_docker_data()
            self.logger.info(c(f'Reporting {len(data)} docker containers', 'dark_grey'))

            await self.sio.emit(
                event='docker',
                data=data,
                callback=None
                )
        except:
            pass

    async def report_introspection(self):

        self.emit('introspection', self.running)

        if not self.sio or not self.sio.connected:
            return

        try:
            self.logger.info(c(f'Reporting introspection {"running" if self.running else "stopped"}', 'dark_grey'))

            await self.sio.emit(
                event='introspection',
                data=self.running,
                callback=None
                )
        except:
            pass