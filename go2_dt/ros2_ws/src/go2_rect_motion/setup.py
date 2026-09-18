from setuptools import setup
import os
from glob import glob

package_name = 'go2_rect_motion'

setup(
    name=package_name,
    version='0.0.1',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'config'), glob('config/*.json')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='alberto',
    maintainer_email='alberto@example.com',
    description='Rectangle motion controller for Go2 using ROS2',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'rectangle_walk = go2_rect_motion.rectangle_walk:main',
            'grid_route_walk = go2_rect_motion.grid_route_walk:main',
            'zone_loop_patrol = go2_rect_motion.zone_loop_patrol:main',
            'lidar_static_map_guard = go2_rect_motion.lidar_static_map_guard:main',
        ],
    },
)

