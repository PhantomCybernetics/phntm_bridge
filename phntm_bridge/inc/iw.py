import iwlib
import iwlib.iwlist
import asyncio

from phntm_interfaces.msg import IWStatus
from builtin_interfaces.msg import Time as Stamp
from std_msgs.msg import Header

import time
import math

from rclpy.node import Node, Parameter, Subscription, QoSProfile, Publisher
from rclpy.qos import QoSHistoryPolicy, QoSReliabilityPolicy, DurabilityPolicy

from termcolor import colored as c
import os

class IW:
    def __init__(self, iface:str, monitor_period_s:float, node:Node, topic:str):
        self.iface:str = iface
        self.monitor_period:float = monitor_period_s
        self.monitor_running:bool = False

        self.max_quality:float = iwlib.utils.get_max_quality(self.iface)
        self.supports_scanning:bool = iwlib.utils.supports_scanning(self.iface)
        self.node:Node = node
        self.pub:Publisher = None
        self.topic:str = topic

        self.last_essid:str = None #roaming between aps with same essid
        self.last_access_point:str = None
        self.last_frequency:float = None #GHz

    async def start_monitor(self):
        if self.monitor_running:
            return;

        qos = QoSProfile(history=QoSHistoryPolicy.KEEP_LAST,
                         depth=1,
                         reliability=QoSReliabilityPolicy.BEST_EFFORT
                         )

        self.pub = self.node.create_publisher(IWStatus, self.topic, qos)

        if self.pub == None:
            self.get_logger().error(f'Failed creating publisher for topic {self.topic}, protocol={self.protocol}, peer={id_peer}')
            return False

        self.monitor_running = True
        print(c(f'IW Monitor started w pub={self.pub}', 'cyan'))

        try:
            while self.monitor_running:
                await self.report_status()
                await asyncio.sleep(self.monitor_period)
        except (asyncio.CancelledError, KeyboardInterrupt):
            print('IW Monitor stoped')
            return
        except Exception as e:
            raise e

    async def report_status(self):

        cfg = await asyncio.get_event_loop().run_in_executor(None, iwlib.iwconfig.get_iwconfig, self.iface)
        msg = IWStatus()
        # msg.header = Header()
        # msg.header.stamp = Stamp(

        time_nanosec:int = time.time_ns()
        msg.header.stamp.sec = math.floor(time_nanosec / 1000000000)
        msg.header.stamp.nanosec = time_nanosec - (msg.header.stamp.sec *1000000000)

        try:
            if 'Frequency' in cfg:
                msg.frequency = float(cfg['Frequency'].split()[0]) # b'5.24 GHz'
            if 'Access Point' in cfg:
                msg.access_point = cfg['Access Point'].decode() # b'BA:FB:E4:45:19:4F'
            if 'BitRate' in cfg:
                msg.bit_rate = float(cfg['BitRate'].split()[0]) # b'120 Mb/s'
            if 'ESSID' in cfg:
                msg.essid = cfg['ESSID'].decode() # b'CircuitLaunch'
            if 'Mode' in cfg:
                if cfg['Mode'] == b'Managed':
                    msg.mode = IWStatus.MODE_MANAGED #b'Managed'
                elif cfg['Mode'] == b'Ad-Hoc':
                    msg.mode = IWStatus.MODE_AD_HOC #b'Ad-Hoc'
            if 'stats' in cfg:
                if 'quality' in cfg['stats']:
                    msg.quality = cfg['stats']['quality'] # 34
                if 'level' in cfg['stats']:
                    msg.level = cfg['stats']['level'] # 180
                if 'noise' in cfg['stats']:
                    msg.noise = cfg['stats']['noise'] # 0

            msg.quality_max = self.max_quality # 70
            msg.supports_scanning = self.supports_scanning

            self.last_essid = msg.essid
            self.last_access_point = msg.access_point
            self.last_frequency = msg.frequency

        except Exception as e:
            print (c(f'Error while generating IWStatus: {e}', 'red'))
            print (f'IW CFG was: {cfg}')

        asyncio.get_event_loop().run_in_executor(None, self.pub.publish, msg)


    async def stop_monitor(self):
        if not self.monitor_running:
            return

        print(c(f'IW Monitor stopping...', 'cyan'))
        self.monitor_running = False #kill the loop

        if self.pub:
            self.pub.destroy()
            self.pub = None


    async def scan(self, roam:bool):
        print(c(f'IW Monitor scanning... roam={roam}', 'cyan'))
        results = await asyncio.get_event_loop().run_in_executor(None, iwlib.iwlist.scan, self.iface)
        res_data = []
        print(f'IW Monitor scan results: ')

        roaming_candidates = []

        for one_res in results:
            one_data = {}
            linehr = []

            if 'ESSID' in one_res.keys():
                one_data['essid'] = one_res['ESSID'].decode() # b'CircuitLaunch'
                linehr.append(f'ESSID: {one_data["essid"]}')

            if 'Access Point' in one_res.keys():
                one_data['access_point'] = one_res['Access Point'].decode() # b'BA:FB:E4:45:19:4F'
                linehr.append(f'AP: {one_data["access_point"]}')

            if 'Frequency' in one_res.keys():
                one_data['frequency'] = float(one_res['Frequency'].split()[0]) # b'5.24 GHz'
                linehr.append(f'Freq: {one_data["frequency"]} GHz')

            if 'BitRate' in one_res.keys():
                one_data['bit_rate'] = float(one_res['BitRate'].split()[0]) # b'120 Mb/s'
                linehr.append(f'BR: {one_data["bit_rate"]} Mb/s')

            if 'Mode' in one_res.keys():
                if one_res['Mode'] == b'Managed':
                    one_data['mode'] = IWStatus.MODE_MANAGED #b'Managed'
                elif one_res['Mode'] == b'Ad-Hoc':
                    one_data['mode'] = IWStatus.MODE_AD_HOC #b'Ad-Hoc'
                elif one_res['Mode'] == b'Master':
                    one_data['mode'] = 3 #b'Ad-Hoc'
                linehr.append(f'Mode: {one_data["mode"]}')

            if 'stats' in one_res.keys():
                if 'quality' in one_res['stats'].keys():
                    one_data['quality'] = one_res['stats']['quality'] # 34
                    linehr.append(f'Q: {one_data["quality"]}')

                if 'level' in one_res['stats'].keys():
                    one_data['level'] = one_res['stats']['level'] # 180
                    linehr.append(f'Lvl: {one_data["level"]}')

                if 'noise' in one_res['stats'].keys():
                    one_data['noise'] = one_res['stats']['noise'] # 0
                    linehr.append(f'N: {one_data["noise"]}')

                if 'updated' in one_res['stats'].keys():
                    one_data['updated'] = one_res['stats']['updated']
                    linehr.append(f'Upd: {one_data["updated"]}')

            if 'essid' in one_data and one_data['essid'] == self.last_essid \
                and 'access_point' in one_data \
                and 'quality' in one_data and 'level' in one_data:
                roaming_candidates.append(one_data)

            res_data.append(one_data)
            print(c(', '.join(linehr), 'cyan'))

        if roam:
            print(f'IW: Seeing {len(roaming_candidates)} roaming candidates for {self.last_essid}')
            # sort by quallity desc
            sorted_candidates = sorted(roaming_candidates, key=lambda x: x['level'], reverse=True)
            for cand in sorted_candidates:
                print(f" >> {cand['access_point']} {cand['frequency'] if 'frequency' in cand else 'n/a'} GHz, Quality={cand['quality']}, lvl={cand['level'] if 'level' in cand else 'n/a'}, noise={cand['noise'] if 'noise' in cand else 'n/a'} {' < CURR' if cand['access_point'] == self.last_access_point else ''}")

            bestest = sorted_candidates[0]
            if bestest['access_point'] == self.last_access_point:
                print(c(f" >>> Not roaming, current AP seems best", 'cyan'))
            else:
                print(c(f" >>> Attenmpting to roam to {bestest['access_point']} with quality={bestest['quality']}", 'cyan'))
                wpa_cli_res = await asyncio.get_event_loop().run_in_executor(None, os.system, f"wpa_cli -p /host_run/wpa_supplicant/ -i {self.iface} roam {bestest['access_point']}")
                print(f'wpa_cli_res={wpa_cli_res}')

        return res_data

