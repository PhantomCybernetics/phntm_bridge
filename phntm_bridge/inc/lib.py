import asyncio

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel, RTCConfiguration, RTCIceServer

from rclpy.node import Node, Parameter, Subscription, QoSProfile, Publisher
from rclpy.duration import Duration, Infinite
from rclpy.impl.rcutils_logger import RcutilsLogger
from rosidl_runtime_py.utilities import get_message, get_interface
from rclpy.callback_groups import CallbackGroup
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy

import subprocess
import os
import time
import math
import docker
import tarfile, io
import requests
import json

def qos_equal(a:QoSProfile, b:QoSProfile) -> bool:
    if not a and b or not b and a:
        return False
    
    if a.depth != b.depth:
        return False
    if a.history != b.history:
        return False
    if a.reliability != b.reliability:
        return False
    if a.durability != b.durability:
        return False
    if a.lifespan != b.lifespan:
        return False
    if a.deadline != b.deadline:
        return False

    return True


def set_message_header(node, msg):
    time_nanosec:int = time.time_ns()
    msg.header.stamp.sec = math.floor(time_nanosec / 1000000000)
    msg.header.stamp.nanosec = time_nanosec % 1000000000
    msg.header.frame_id = node.hostname


def format_bytes(b, mib=False):        
    unit = 1000
    GB = unit * unit * unit # 
    MB = unit * unit # docker stats shows MiB, keep consistent
    KB = unit
    
    if b > GB:
        return f'{(b / GB):.2f}{"GiB" if mib else "GB"}'
    elif b > MB:
        return f'{(b / MB):.2f}{"MiB" if mib else "MB"}'
    elif b > KB:
        return f'{(b / KB):.2f}{"KiB" if mib else "KB"}'
    elif b > 0:
        return f'{(b):.2f}B'
    else:
        return f'0B'
    
async def locate_file(file_url:str, ros_distro:str, docker_client:docker.DockerClient, logger:RcutilsLogger):
    
    pkg:str = None
    pkg_prefix = ""
    
    if file_url.startswith('file:/'):
        file_url = file_url.replace('file://', '')
        file_url = file_url.replace('file:/', '')
        if not file_url.startswith('/'):
            file_url = '/' + file_url
        logger.info(f'Bridge requesting file {file_url}')
        
    elif file_url.startswith('package:/'):
        file_url = file_url.replace('package://', '')
        file_url = file_url.replace('package:/', '')
        
        parts = file_url.split('/')
        pkg = parts[0]
        
        # file_url = file_url.replace(f'{pkg}/', '')
        
        if not file_url.startswith('/'):
            file_url = '/' + file_url
        
        logger.info(f'Bridge requesting file {file_url} in pkg {pkg}')
        
        if pkg is not None:
            res = subprocess.run([f"/opt/ros/{ros_distro}/bin/ros2", "pkg", "prefix", pkg], capture_output=True)
            if res.stdout:
                pkg_prefix = res.stdout.decode("ASCII").rstrip() + '/share'
                logger.debug(f'local ros2 {ros_distro} pkg prefix is {pkg_prefix}')
            else:
                logger.debug(f'local ros2 {ros_distro} pkg prefix for {pkg} not found in this fs')
    else:
        logger.error(f'Bridge requesting invalid file {file_url}')
        return None # file not found
    
    if pkg_prefix and os.path.isfile(pkg_prefix + file_url):
        logger.debug(f'File found in this fs (pkg_prefix={pkg_prefix})')
        f = open(file_url, "rb")
        res = f.read()
        f.close()
        return res
    
    elif os.path.isfile(file_url):
        logger.debug(f'File found in this fs')
        f = open(file_url, "rb")
        res = f.read()
        f.close()
        return res
    
    elif docker_client:
        logger.debug(f'File not found in this fs, searching Docker containers...')
        docker_containers = docker_client.containers.list(all=False)
        for container in docker_containers:
            pkg_prefix = ""    
            if pkg:
                cmd = f'/bin/bash -c "export PS1=phntm && . /opt/ros/{ros_distro}/setup.bash && . ~/.bashrc && /opt/ros/{ros_distro}/bin/ros2 pkg prefix {pkg}"'
                res = container.exec_run(cmd)
                if res.exit_code == 1:
                    logger.debug(f'pkg not found in cont {container.name} \nout={res.output}\ncmd={cmd}')
                else:
                    pkg_prefix = res.output.decode("ASCII").rstrip() + '/share'
                    logger.debug(f'cont {container.name} has pkg in {pkg_prefix}')
            
            try:
                tar_chunks, stats = container.get_archive(pkg_prefix+file_url, chunk_size=None, encode_stream=False)
            except Exception as e:
                logger.debug(f'File not found in {container.name} fs')
                continue
            
            logger.debug(f'File found in {container.name} fs')
            logger.debug(str(stats))
            
            b_arr = []
            for chunk in tar_chunks:
                b_arr.append(chunk)
            
            tar_bytes = b''.join(b_arr)
            
            logger.debug(f' Making tar obj w {len(tar_bytes)} B')
            
            file_like_object = io.BytesIO(tar_bytes)
            tar = tarfile.open(fileobj=file_like_object)

            logger.debug(f' Tar memebers: {tar.getnames()}')

            member = tar.getmember(stats['name'])
            
            logger.debug(f' {member} data starts at {member.offset_data}')
            
            res_bytes = tar_bytes[member.offset_data : member.offset_data+stats['size']]
            return res_bytes
            
    return None # file not found

async def upload_file_bytes(file_url:str, file_bytes:bytes, id_robot:str, auth_key:str, upload_host:str, logger:RcutilsLogger):
    
    chunk_size = 1024*1024
    byte_size = len(file_bytes)
    total_parts = math.ceil(byte_size / chunk_size)
    
    logger.info(f' Uploading {byte_size}B as {total_parts} chunks')
    
    offset = 0
    for index in range(total_parts):
        chunk = file_bytes[offset : offset+chunk_size]
        offset += chunk_size
        files = {
            'file': (f'{file_url}.part{index}', chunk)
        }
        data = {
            'idRobot': id_robot,
            'key': auth_key,
            'fileUrl': file_url
        }
        response = requests.post(f'{upload_host}/upload', files=files, data={'json': json.dumps(data)})
        
        if response.status_code != 200:
            logger.error(f"Error uploading chunk {index}: {response.text}")
            return None # err
        
        logger.debug(f"Uploaded chunk {index + 1}/{total_parts}")

    # Complete the upload
    complete_data = {
        'idRobot': id_robot,
        'key': auth_key,
        'fileUrl': file_url,
        'totalParts': total_parts
    }
    complete_response = requests.post(f'{upload_host}/complete', json=complete_data)

    if complete_response.status_code == 200:
        logger.debug("File upload and reassembly completed")
        return json.loads(complete_response.text)
    else:
        logger.error(f"Error completing upload: {complete_response.text}")
        return None