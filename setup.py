from setuptools import setup
import os
from glob import glob

package_name = 'phntm_webrtc_bridge'

setup(
    name=package_name,
    version='0.0.1',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
         # Include all launch files.
        (os.path.join('share', package_name, 'launch'), glob(os.path.join('launch', '*launch.[pxy][yma]*')))
    ],
    install_requires=['setuptools==58.2.0'],
    zip_safe=True,
    maintainer='Mirek Burkon',
    maintainer_email='mirek@phntm.io',
    description='Bidirectional WebRTC ROS bridge for fast P2P data visualization and human-robot interaction',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'bridge = phntm_webrtc_bridge.bridge:main',
            'talker = phntm_webrtc_bridge.publisher_member_function:main',
            'listener = phntm_webrtc_bridge.subscriber_member_function:main',

        ],
    },
)
