from setuptools import find_packages, setup

package_name = 'go2_dt_bridge'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='nextnet',
    maintainer_email='nextnet@todo.todo',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
	entry_points={
	    'console_scripts': [
	        'state_bridge = go2_dt_bridge.state_bridge:main',
	    ],
	},
)
