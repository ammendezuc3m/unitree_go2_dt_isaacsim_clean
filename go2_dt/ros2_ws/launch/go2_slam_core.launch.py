#!/usr/bin/env python3

import os
from pathlib import Path

from ament_index_python.packages import get_package_share_directory

from launch import LaunchDescription
from launch.actions import ExecuteProcess, IncludeLaunchDescription, DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch.launch_description_sources import PythonLaunchDescriptionSource

from launch_ros.actions import Node


def generate_launch_description():
    ws_root = Path(os.path.expanduser("~/AlbertoDir/go2_dt/ros2_ws"))

    robot_ip = os.getenv("ROBOT_IP", "192.168.12.1")
    robot_token = os.getenv("ROBOT_TOKEN", "")
    conn_type = os.getenv("CONN_TYPE", "webrtc")
    robot_ip_list = robot_ip.replace(" ", "").split(",") if robot_ip else []

    go2_pkg = get_package_share_directory("go2_robot_sdk")
    slam_pkg = get_package_share_directory("slam_toolbox")

    urdf_path = os.path.join(go2_pkg, "urdf", "go2.urdf")
    slam_config = str(ws_root / "config" / "go2_mapper_params_online_async.yaml")

    with open(urdf_path, "r") as f:
        robot_description = f.read()

    use_sim_time = LaunchConfiguration("use_sim_time", default="false")

    return LaunchDescription([
        DeclareLaunchArgument(
            "use_sim_time",
            default_value="false",
            description="Use simulation time",
        ),

        # ------------------------------------------------------------
        # Robot description / TF estáticos del Go2
        # ------------------------------------------------------------
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            name="go2_robot_state_publisher",
            output="screen",
            parameters=[{
                "use_sim_time": use_sim_time,
                "robot_description": robot_description,
            }],
        ),

        # ------------------------------------------------------------
        # Driver principal del Go2
        # Expone: /odom, /pose, /imu, /joint_states, /camera/image_raw,
        # /go2_states, /webrtc_req, etc.
        # ------------------------------------------------------------
        Node(
            package="go2_robot_sdk",
            executable="go2_driver_node",
            name="go2_driver_node",
            output="screen",
            parameters=[{
                "robot_ip": robot_ip,
                "token": robot_token,
                "conn_type": conn_type,
            }],
        ),

        # ------------------------------------------------------------
        # LiDAR Unitree L2 -> PointCloud2
        # ------------------------------------------------------------
        Node(
            package="lidar_processor_cpp",
            executable="lidar_to_pointcloud_node",
            name="lidar_to_pointcloud",
            output="screen",
            remappings=[
                ("robot0/point_cloud2", "point_cloud2"),
            ],
            parameters=[{
                "robot_ip_lst": robot_ip_list,
                "map_name": "go2_raw_lidar_debug",
                "map_save": "false",
            }],
        ),

        # ------------------------------------------------------------
        # Agregador/filtro de nube
        # Publica /pointcloud/filtered
        # ------------------------------------------------------------
        Node(
            package="lidar_processor_cpp",
            executable="pointcloud_aggregator_node",
            name="pointcloud_aggregator",
            output="screen",
            parameters=[{
                "max_range": 12.0,
                "min_range": 0.20,
                "height_filter_min": -0.20,
                "height_filter_max": 1.80,
                "downsample_rate": 1,
                "publish_rate": 10.0,
            }],
        ),

        # ------------------------------------------------------------
        # PointCloud2 -> LaserScan bruto
        # Publica /scan_raw
        # ------------------------------------------------------------
        Node(
            package="pointcloud_to_laserscan",
            executable="pointcloud_to_laserscan_node",
            name="go2_pointcloud_to_laserscan",
            output="screen",
            remappings=[
                ("cloud_in", "/pointcloud/filtered"),
                ("scan", "/scan_raw"),
            ],
            parameters=[{
                "target_frame": "base_link",
                "transform_tolerance": 0.2,

                "min_height": -0.20,
                "max_height": 1.80,

                "angle_min": -3.14159,
                "angle_max": 3.14159,
                "angle_increment": 0.00872665,

                "scan_time": 0.10,
                "range_min": 0.30,
                "range_max": 12.0,
                "use_inf": True,
                "concurrency_level": 1,
            }],
        ),

        # ------------------------------------------------------------
        # Filtro específico Go2
        # /scan_raw -> /scan
        # Elimina puntos del propio chasis/frontal cercano.
        # ------------------------------------------------------------
        ExecuteProcess(
            cmd=[
                "python3",
                str(ws_root / "lidar_nodes" / "go2_scan_filter.py"),
                "--ros-args",
                "-p", "input_scan_topic:=/scan_raw",
                "-p", "output_scan_topic:=/scan",

                "-p", "range_min_out:=0.30",
                "-p", "range_max_out:=12.0",

                "-p", "remove_close_radius_m:=0.25",

                "-p", "filter_front_cone:=true",
                "-p", "front_cone_half_angle_rad:=0.70",
                "-p", "front_cone_distance_m:=0.60",

                "-p", "filter_rear_cone:=false",
            ],
            output="screen",
        ),

        # ------------------------------------------------------------
        # Relay para que tus scripts antiguos sigan publicando en
        # /cmd_vel_out, pero el SDK reciba /cmd_vel.
        # ------------------------------------------------------------
        ExecuteProcess(
            cmd=[
                "python3",
                str(ws_root / "lidar_nodes" / "cmd_vel_out_relay.py"),
                "--ros-args",
                "-p", "input_topic:=/cmd_vel_out",
                "-p", "output_topic:=/cmd_vel",
            ],
            output="screen",
        ),

        # ------------------------------------------------------------
        # SLAM Toolbox
        # Publica: /map y TF map -> odom
        # ------------------------------------------------------------
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(slam_pkg, "launch", "online_async_launch.py")
            ),
            launch_arguments={
                "slam_params_file": slam_config,
                "use_sim_time": use_sim_time,
            }.items(),
        ),

        # ------------------------------------------------------------
        # Detector de novedades sobre SLAM
        # Lee: /map + /scan + TF
        # Escribe: live_lidar_state.json
        # ------------------------------------------------------------
        ExecuteProcess(
            cmd=[
                "python3",
                str(ws_root / "lidar_nodes" / "slam_novelty_detector.py"),
                "--ros-args",
                "-p", "map_topic:=/map",
                "-p", "scan_topic:=/scan",
                "-p", "state_path:=/home/nextnet/AlbertoDir/go2_dt/ros2_ws/lidar_outputs/live_lidar_state.json",

                "-p", "map_frame:=map",
                "-p", "base_frame:=base_link",

                "-p", "baseline_capture_delay_sec:=8.0",
                "-p", "occupied_threshold:=55",

                "-p", "dilation_cells:=3",
                "-p", "min_range_m:=0.35",
                "-p", "max_range_m:=8.0",

                "-p", "novel_min_points:=10",
                "-p", "cluster_connect_radius_cells:=2",
                "-p", "cluster_min_cells:=6",

                "-p", "confirm_frames:=3",
                "-p", "clear_frames:=4",
                "-p", "state_write_period_sec:=0.10",
            ],
            output="screen",
        ),
    ])
