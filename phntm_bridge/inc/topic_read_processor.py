import asyncio
import concurrent.futures

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel, RTCConfiguration, RTCIceServer

from rclpy.node import Node, Parameter, Subscription, QoSProfile, Publisher
from rclpy.duration import Duration, Infinite
from rclpy.impl.rcutils_logger import RcutilsLogger
from rosidl_runtime_py.utilities import get_message, get_interface
from rclpy.callback_groups import CallbackGroup
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy

from termcolor import colored as c

import rclpy

from typing import Callable
import time
from rclpy.callback_groups import CallbackGroup, MutuallyExclusiveCallbackGroup
from rclpy.context import Context
from rclpy.executors import Executor, MultiThreadedExecutor, SingleThreadedExecutor
import threading

import multiprocessing as mp
from queue import Empty, Full


def on_topic_msg(topic:str, msg:any, newest:dict[str:any]):
    # reader_node.get_logger().info(f' >> {msg_topic}, got {len(msg)} B')
   newest[topic] = msg

def on_cmd(reader_node:Node, ctrl_cmd:tuple, subs:dict[str:Subscription], newest:dict[str:any]):

    subscribe = ctrl_cmd[0] == 'subscribe'
    topic = ctrl_cmd[1]

    if not subscribe or topic in subs.keys():
        reader_node.get_logger().info(f'Destroying local subscriber for {topic}')
        reader_node.destroy_subscription(subs[topic])
        del subs[topic]

    if subscribe:

        protocol = ctrl_cmd[2]
        reliability = ctrl_cmd[3]
        durability = ctrl_cmd[4]

        message_class = None
        try:
            message_class = get_message(protocol)
        except:
            pass
        if message_class == None:
            reader_node.get_logger().error(f'NOT subscribing to topic {topic}, msg class {protocol} not loaded')
            return

        qosProfile = QoSProfile(history=QoSHistoryPolicy.KEEP_LAST,
                    depth=1,
                    reliability=reliability,
                    durability=durability,
                    lifespan=Infinite
                    )
        reader_node.get_logger().warn(f'Subscribing to topic {topic} {protocol}')

        subs[topic] = reader_node.create_subscription(
                        msg_type=message_class,
                        topic=topic,
                        callback=lambda msg: on_topic_msg(topic, msg, newest),
                        qos_profile=qosProfile,
                        raw=True,
                    )
        if not topic in subs.keys():
            reader_node.get_logger().error(f'Failed subscribing to topic {topic}, msg class={protocol}')

# runs as a separate process for bette isolation and lower ctrl/cam latency
def TopicReadProcessor(running_shared:mp.Value, ctrl_queue:mp.Queue, data_out_queue:mp.Queue,
                        conf:any,
                        log_message_every_sec:float=5.0):

    print('TopicReadProcessor: starting')

    rcl_ctx = Context()
    rcl_ctx.init() # This must be done before any ROS nodes can be created.
    # rcl_cbg = MutuallyExclusiveCallbackGroup()
    rcl_executor = MultiThreadedExecutor(context=rcl_ctx)
    reader_node = Node("phntm_bridge_reader", context=rcl_ctx, enable_rosout=False)

    rcl_executor.add_node(reader_node)
    # rcl_cbg.add_entity(reader_node)
    # rcl_cbg.add_entity(rcl_ctx)
    # rcl_cbg.add_entity(rcl_executor)

    reader_node.get_logger().set_level(rclpy.logging.LoggingSeverity.DEBUG)
    # reader_node.load_config(self.get_logger())

    subs:dict[str:Subscription] = {}
    newest:dict[str:any] = {}

    try:
        while running_shared.value > 0:
            # print(c(f'yellow! {reader_node}', 'yellow'))

            while True: #recieve cmd messages
                try:
                    ctrl_cmd = ctrl_queue.get(block=False)
                    on_cmd(reader_node, ctrl_cmd, subs, newest)
                except Empty:
                    break # all messages processed

            # spin the node
            rcl_executor.spin_once(timeout_sec=1.0) #initially times out before tehre is anything to do (subscribers)

            for topic in newest.keys():
                try:
                    data_out_queue.put_nowait([topic, newest[topic]]) #put in out queue
                except Full:
                    reader_node.get_logger().warn(f' >> out queue full, dropping {topic} msg')
                    pass
            newest.clear()
            time.sleep(.1)

    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    except Exception as e:
        print(c(f'Exception in TopicReadProcessor:', 'red'))
        print(e)

    reader_node.destroy_node()
    rcl_executor.shutdown()

    print('TopicReadProcessor: exiting')