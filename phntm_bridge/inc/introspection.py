import asyncio
from rclpy.timer import Timer
from rclpy.impl.rcutils_logger import RcutilsLogger
from rclpy.callback_groups import CallbackGroup
from rclpy.context import Context
from rclpy.node import Node, Parameter, QoSProfile, Publisher
from rosidl_runtime_py.utilities import get_message, get_interface
from rosidl_runtime_py import get_interface_path
from rclpy_message_converter import message_converter

import rclpy
import os
import subprocess
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy

from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy
from rclpy.duration import Duration, Infinite
from pprint import pprint
from .lib import qos_equal

try:
    from .camera import get_camera_info
except ModuleNotFoundError:
    pass
import socketio
from termcolor import colored as c
import time

from .peer import WRTCPeer
import traceback

from pyee.base import EventEmitter
from pyee.asyncio import AsyncIOEventEmitter
from phntm_interfaces.msg import DockerStatus, DockerContainerStatus
from rclpy.serialization import deserialize_message

class Introspection (AsyncIOEventEmitter):

    def __init__(self, period:float, stop_after:float, ctrl_node:Node, sio:socketio.AsyncClient):
        super().__init__()
        self.period:float = period
        self.stop_after:float = stop_after

        self.ctrl_node:Node = ctrl_node
        self.logger = ctrl_node.get_logger()

        self.picam2 = ctrl_node.picam2
        self.docker_monitor_topic:str = self.ctrl_node.get_parameter(f'docker_monitor_topic').get_parameter_value().string_value
        self.discovered_topics:dict[str: dict['msg_type':str]] =  {}
        self.discovered_idls:dict[str:str] = {}
        self.discovered_services:dict[str: dict['msg_type':str]] = {}
        self.discovered_cameras:dict[str: any] = {}
        self.discovered_docker_containers:dict[str: DockerStatus] = {} # last received docker status message by host
        self.discovered_nodes:dict[str: dict[
            'namespace':str,
            'publishers':dict[str:dict['msg_type':str,'qos':QoSProfile]],
            'subscribers':dict[str:dict['msg_type':str,'qos':QoSProfile]],
            'services':dict[str:list[str]]
        ]] =  {}

        self.sio:socketio.AsyncClient = sio
        self.running:bool = False

        self.waiting_peers:list = []
        
        self.devnull = open(os.devnull, 'w')


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
            new_topics_or_cameras_discovered:bool = await self.run_discovery() # True if cameras or topics discovered

            for peer in self.waiting_peers.copy():
                update_peer = new_topics_or_cameras_discovered
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
        if msg_type in self.ctrl_node.blacklist_msg_types:
            return False
        
        if msg_type in self.discovered_idls:
            return False # no change
        try:
            idl_path = get_interface_path(msg_type)
        except Exception as e:
            self.logger.error(f'Interface path not found for {msg_type}, {e}')
            return False # no change
        
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

    async def start_docker_subscription(self, topic:str):
        
        qosProfile = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.SYSTEM_DEFAULT,
            lifespan=Infinite
        )
        
        await self.ctrl_node.subscribe_data_topic(topic,
                                                  qos=qosProfile,
                                                  peer=None,
                                                  msg_callback=self._on_docker_message)
    
    async def stop_docker_subscription(self, topic:str):
        await self.ctrl_node.unsubscribe_data_topic(topic, peer=None, msg_callback=self._on_docker_message)
             

    async def _on_docker_message(self, raw_msg:DockerStatus):
        
        msg:DockerStatus = deserialize_message(raw_msg, DockerStatus)
        
        host = 'phntm_agent_'+msg.header.frame_id if msg.header.frame_id else 'phntm_agent' # default frame id is empty
        docker_containers_changed = False
        
        if not host in self.discovered_docker_containers:
            docker_containers_changed = True
        elif len(msg.containers) != len(self.discovered_docker_containers[host].containers):
            docker_containers_changed = True
       
        if host in self.discovered_docker_containers:
            for i in range(len(msg.containers)):
                if msg.containers[i].id != self.discovered_docker_containers[host].containers[i].id \
                or msg.containers[i].name != self.discovered_docker_containers[host].containers[i].name \
                or msg.containers[i].status != self.discovered_docker_containers[host].containers[i].status:
                    docker_containers_changed = True
                    break
        else: # new host id, try looking for hosts we have and match id by container names
            old_hosts = self.discovered_docker_containers.keys()
            for i in range(len(msg.containers)):
                for old_host in old_hosts:
                    for j in range(len(self.discovered_docker_containers[old_host].containers)):
                        if msg.containers[i].id == self.discovered_docker_containers[old_host].containers[j].id:
                            # agent host changed
                            self.logger.info(c(f'Agent host {old_host} => { msg.containers[i].id}; removing old'))    
                            del self.discovered_docker_containers[old_host]
                            docker_containers_changed = True
                            break
                            
        self.discovered_docker_containers[host] = msg
        
        if docker_containers_changed:
            
            await self.report_docker()
            
            # # new nodes and topics aren't detected (on Humble) when started after the bridge node
            # # calling ros2cli topic list seems to flush the cache, so trigering it when a change is
            # # detected in docker containers
            try:
                self.logger.info(c(f'Docker containers changed, calling ros-cli topic list to flush the cache'))    
                process = subprocess.Popen(['ros2', 'topic', 'list'],
                                                stdout=self.devnull, 
                                                stderr=subprocess.STDOUT)
                process.wait()
            except Exception as e:
                self.logger.info(c(f'Exception while calling ros2cli topic list: {e}'))
            # 
            # make sure instrospection is running as there are likely things to be detected
            if not self.running:
                asyncio.get_event_loop().create_task(self.start())
                

    # spinned by timer
    async def run_discovery(self) -> bool:

        nodes_changed = False
        idls_changed = False
        services_changed = False
        cameras_changed = False
        topics_changed = False
        
        try:
            wating_for = set()
            for peer in self.waiting_peers:
                for topic in peer.topics_not_discovered:
                    wating_for.add(topic)
                for cam in peer.cameras_not_discovered:
                    wating_for.add(cam)

            pub_info_by_topic:dict[str:list[dict]] = {}
            sub_info_by_topic:dict[str:list[dict]] = {}
            
            # discover nodes
            self.logger.info(c(f'Introspecting... ({len(self.waiting_peers)} peers waiting{(" for " + ", ".join(set(wating_for))) if len(wating_for) > 0 else ""})', 'dark_grey')) 
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

                # node publishers
                try:
                    node_publishers = await asyncio.get_event_loop().run_in_executor(None, self.ctrl_node.get_publisher_names_and_types_by_node, node, namespace)
                except Exception:
                    node_publishers = []
                for old_pub_topic in self.discovered_nodes[node]['publishers'].copy().keys():
                    pub_found = False
                    for pub_info in node_publishers:
                        if pub_info[0] == old_pub_topic:
                            pub_found = True
                            break
                    if not pub_found:
                        nodes_changed = True
                        del self.discovered_nodes[node]['publishers'][old_pub_topic]
                for pub_info in node_publishers:
                    topic = pub_info[0]
                    msg_type = pub_info[1][0]
                    if topic in self.ctrl_node.blacklist_topics or msg_type in self.ctrl_node.blacklist_topics:
                        continue
                    
                    # fetch and cache topic pubs details
                    if not topic in pub_info_by_topic.keys():
                        try:
                            pub_info_by_topic[topic] = await asyncio.get_event_loop().run_in_executor(None, self.ctrl_node.get_publishers_info_by_topic, topic)
                        except Exception:
                            pub_info_by_topic[topic] = []
                    pub_qos = None
                    for pti in pub_info_by_topic[topic]:
                        if pti.node_name == node and pti.node_namespace == namespace:
                            pub_qos = pti.qos_profile
                            
                    if not topic in self.discovered_nodes[node]['publishers'].keys() \
                        or self.discovered_nodes[node]['publishers'][topic]['msg_type'] != msg_type \
                        or not qos_equal(pub_qos, self.discovered_nodes[node]['publishers'][topic]['qos']):
                        self.discovered_nodes[node]['publishers'][topic] = {
                            'msg_type': msg_type,
                            'qos': pub_qos
                        }
                        self.logger.info(c(f'{node} > {topic} [{msg_type}]', 'light_green'))
                        # self.logger.info(c(f'{str(pub_qos)}', 'light_green'))
                        nodes_changed = True

                # node subscribers
                try:
                    node_subscribers = await asyncio.get_event_loop().run_in_executor(None, self.ctrl_node.get_subscriber_names_and_types_by_node, node, namespace)
                except Exception:
                    node_subscribers = []
                for old_sub_topic in self.discovered_nodes[node]['subscribers'].copy().keys():
                    sub_found = False
                    for sub_info in node_subscribers:
                        if sub_info[0] == old_sub_topic:
                            sub_found = True
                            break
                    if not sub_found:
                        nodes_changed = True
                        del self.discovered_nodes[node]['subscribers'][old_sub_topic]
                for sub_info in node_subscribers:
                    topic = sub_info[0]
                    msg_type = sub_info[1][0]
                    if topic in self.ctrl_node.blacklist_topics or msg_type in self.ctrl_node.blacklist_topics:
                        continue
                    
                    # fetch and cache topic subs details
                    if not topic in sub_info_by_topic.keys():
                        try:
                            sub_info_by_topic[topic] = await asyncio.get_event_loop().run_in_executor(None, self.ctrl_node.get_subscriptions_info_by_topic, topic)
                        except Exception:
                            sub_info_by_topic[topic] = []
                    sub_qos = None
                    for sti in sub_info_by_topic[topic]:
                        if sti.node_name == node and sti.node_namespace == namespace:
                            sub_qos = sti.qos_profile
                    
                    if not topic in self.discovered_nodes[node]['subscribers'].keys() \
                        or self.discovered_nodes[node]['subscribers'][topic]['msg_type'] != msg_type \
                        or not qos_equal(sub_qos, self.discovered_nodes[node]['subscribers'][topic]['qos']):
                            
                        self.discovered_nodes[node]['subscribers'][topic] = {
                            'msg_type': msg_type,
                            'qos': sub_qos
                        }
                        self.logger.info(c(f'{node} < {topic} {msg_type}', 'light_green'))
                        # self.logger.info(c(f'{str(sub_qos)}', 'light_green'))
                        nodes_changed = True

                # node services
                try:
                    node_services = await asyncio.get_event_loop().run_in_executor(None, self.ctrl_node.get_service_names_and_types_by_node, node, namespace)
                except Exception:
                    node_services = []
                for old_serv in self.discovered_nodes[node]['services'].copy().keys():
                    serv_found = False
                    for serv_info in node_services:
                        if serv_info[0] == old_serv:
                            serv_found = True
                            break
                    if not serv_found:
                        nodes_changed = True
                        del self.discovered_nodes[node]['services'][old_serv]
                for serv_info in node_services:
                    id_service = serv_info[0]
                    msg_type = serv_info[1][0]
                    if id_service in self.ctrl_node.blacklist_services or msg_type in self.ctrl_node.blacklist_services:
                        continue
                    if not id_service in self.discovered_nodes[node]['services'].keys() \
                    or self.discovered_nodes[node]['services'][id_service] != msg_type:
                        self.discovered_nodes[node]['services'][id_service] = msg_type
                        if len(serv_info[1]) > 1:
                            self.logger.info(c(f'srv {id_service} @ {node} [{msg_type}] ?? {str(serv_info[1])}', 'red'))
                        else:
                            self.logger.info(c(f'srv {id_service} @ {node} [{msg_type}]', 'light_green'))
                        nodes_changed = True
            
            # flat topics + extract message type idls
            flat_topics =  await asyncio.get_event_loop().run_in_executor(None, self.ctrl_node.get_topic_names_and_types)
            for topic_info in flat_topics:
                topic = topic_info[0]
                if topic in self.ctrl_node.blacklist_topics:
                    continue
                if not topic in self.discovered_topics:
                    msg_type = topic_info[1][0]
                    if msg_type in self.ctrl_node.blacklist_topics:
                        continue
                    self.discovered_topics[topic] = { 'msg_type': msg_type }
                    topics_changed = True
                    self.logger.info(c(f'Discovered topic {topic} [{msg_type}]', 'light_blue'))
                    idls_changed = await self.collect_idls(msg_type) or idls_changed

                    if topic == self.docker_monitor_topic:
                        await self.start_docker_subscription(topic)
                    
            # flat services + extract message type idls
            flat_services =  await asyncio.get_event_loop().run_in_executor(None, self.ctrl_node.get_service_names_and_types)
            for service_info in flat_services:
                service = service_info[0]
                if service in self.ctrl_node.blacklist_services:
                    continue
                if not service in self.discovered_services:
                    msg_type = service_info[1][0]
                    if msg_type in self.ctrl_node.blacklist_services:
                        continue
                    self.discovered_services[service] = { 'msg_type': msg_type }
                    services_changed = True
                    self.logger.info(c(f'Discovered service {service} [{msg_type}]', 'magenta'))
                    idls_changed = await self.collect_idls(msg_type) or idls_changed
            
            # check subscribers qos for incompatibility
            if nodes_changed:
                for n in self.discovered_nodes.keys():
                    for topic in self.discovered_nodes[n]['subscribers'].keys():
                        for nn in self.discovered_nodes.keys():
                            if topic in self.discovered_nodes[nn]['publishers']:
                                qos_compat = rclpy.qos.qos_check_compatible(self.discovered_nodes[nn]['publishers'][topic]['qos'], self.discovered_nodes[n]['subscribers'][topic]['qos'])
                                try:
                                    err_index = qos_compat.index(rclpy.qos.QoSCompatibility.ERROR)
                                    msg = qos_compat[err_index+1]
                                    self.logger.error(f'Detected QOS error node {n} {topic}: {msg}')
                                    self.discovered_nodes[n]['subscribers'][topic]['qos_error'] = msg
                                except ValueError as e:
                                    pass
                                try:
                                    err_index = qos_compat.index(rclpy.qos.QoSCompatibility.WARNING)
                                    msg = qos_compat[err_index+1]
                                    self.logger.error(f'Detected QOS warning in node {n} {topic}: {msg}')
                                    self.discovered_nodes[n]['subscribers'][topic]['qos_warning'] = msg
                                except ValueError as e:
                                    pass

            # force add byte to idls as we use it on the client to send heartbeat
            # (but not producing into a topic so no auto type discovery)
            if not 'std_msgs/msg/Byte' in self.discovered_idls: 
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

        except Exception as e:
            self.logger.error(f'Exception in introspection: {e}')
            self.logger.error(traceback.format_exception(e))
            
        return topics_changed or cameras_changed # only topics and cameras keep introspection running


    def get_nodes_data(self) -> dict:
        nodes_data = {}
        for id_node in self.discovered_nodes.keys():
            n = self.discovered_nodes[id_node]
            nodes_data[id_node] = {
                'namespace': n['namespace'],
                'publishers': {},
                'subscribers': {},
                'services': n['services']
            }
        
            for topic in n['publishers'].keys():
                nodes_data[id_node]['publishers'][topic] = {
                    'msg_type': n['publishers'][topic]['msg_type'],
                    'qos': {
                        'depth': n['publishers'][topic]['qos'].depth,
                        'history': n['publishers'][topic]['qos'].history,
                        'reliability': n['publishers'][topic]['qos'].reliability,
                        'durability': n['publishers'][topic]['qos'].durability,
                        'lifespan': (-1 if n['publishers'][topic]['qos'].lifespan == Infinite else n['publishers'][topic]['qos'].lifespan.nanoseconds),
                        'deadline': (-1 if n['publishers'][topic]['qos'].deadline == Infinite else n['publishers'][topic]['qos'].deadline.nanoseconds)
                    }
                }
            for topic in n['subscribers'].keys():
                nodes_data[id_node]['subscribers'][topic] = {
                    'msg_type': n['subscribers'][topic]['msg_type'],
                    'qos': {
                        'depth': n['subscribers'][topic]['qos'].depth,
                        'history': n['subscribers'][topic]['qos'].history,
                        'reliability': n['subscribers'][topic]['qos'].reliability,
                        'durability': n['subscribers'][topic]['qos'].durability,
                        'lifespan': (-1 if n['subscribers'][topic]['qos'].lifespan == Infinite else n['subscribers'][topic]['qos'].lifespan.nanoseconds),
                        'deadline': (-1 if n['subscribers'][topic]['qos'].deadline == Infinite else n['subscribers'][topic]['qos'].deadline.nanoseconds)
                    }
                }
                if 'qos_error' in n['subscribers'][topic]:
                    nodes_data[id_node]['subscribers'][topic]['qos_error'] = n['subscribers'][topic]['qos_error']
                if 'qos_warning' in n['subscribers'][topic]:
                    nodes_data[id_node]['subscribers'][topic]['qos_warning'] = n['subscribers'][topic]['qos_warning']

        return  nodes_data


    async def report_nodes(self):

        self.emit('nodes', self.discovered_nodes.keys())

        if not self.sio or not self.sio.connected:
            return # reports all on socket connect

        try:
            data = self.get_nodes_data()
            self.logger.info(c(f'Reporting {len(data)} nodes', 'dark_grey'))

            await self.sio.emit(
                event='nodes',
                data=data,
                callback=None
                )
        except Exception as e:
            self.logger.error(f'Error pushing nodes data: {e}')
            traceback.print_exc(e)
            pass

    def get_idls_data(self):
        return self.discovered_idls

    async def report_idls(self):

        if not self.sio or not self.sio.connected:
            return # reports all on socket connect

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
            return # reports all on socket connect

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
            return # reports all on socket connect

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
            return # reports all on socket connect

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
        print(self.discovered_docker_containers);
        data = {}
        for host in self.discovered_docker_containers.keys():
            data[host] = message_converter.convert_ros_message_to_dictionary(self.discovered_docker_containers[host])
        return data # latest cached docker status msg per host

    async def report_docker(self):

        if not self.sio or not self.sio.connected:
            return # reports all on socket connect

        try:
            data = self.get_docker_data()
            self.logger.info(c(f'Reporting docker update from host/s: {", ".join(data.keys())}', 'dark_grey'))

            await self.sio.emit(
                event='docker',
                data=data,
                callback=None
                )
        except Exception as e:
            self.logger.error('Exception while reporting Docker data')
            traceback.print_exception(e)
            pass

    async def report_introspection(self):

        self.emit('introspection', self.running)

        if not self.sio or not self.sio.connected:
            return # reports all on socket connect

        try:
            self.logger.info(c(f'Reporting introspection {"running" if self.running else "stopped"}', 'dark_grey'))

            await self.sio.emit(
                event='introspection',
                data=self.running,
                callback=None
                )
        except:
            pass