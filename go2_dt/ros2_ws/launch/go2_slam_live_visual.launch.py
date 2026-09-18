import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, IncludeLaunchDescription
from launch.substitutions import LaunchConfiguration
from launch.launch_description_sources import PythonLaunchDescriptionSource

from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    ws = os.path.expanduser("~/AlbertoDir/go2_dt/ros2_ws")

    robot_ip = os.getenv("ROBOT_IP", "192.168.12.1")
    robot_token = os.getenv("ROBOT_TOKEN", "")
    conn_type = os.getenv("CONN_TYPE", "webrtc")

    go2_pkg = get_package_share_directory("go2_robot_sdk")
    urdf_path = os.path.join(go2_pkg, "urdf", "go2.urdf")

    slam_params = os.path.join(ws, "config", "go2_mapper_params_live.yaml")

    scan_filter_script = os.path.join(ws, "lidar_nodes", "go2_scan_filter.py")
    cmd_vel_relay_script = os.path.join(ws, "lidar_nodes", "cmd_vel_out_relay.py")
    viewer_script = os.path.join(ws, "lidar_nodes", "slam_live_viewer_detector.py")

    with open(urdf_path, "r") as f:
        robot_desc = f.read()

    use_sim_time = LaunchConfiguration("use_sim_time")

    return LaunchDescription([
        DeclareLaunchArgument("use_sim_time", default_value="false"),

        # Robot model / TF estático del Go2.
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            name="go2_robot_state_publisher",
            output="screen",
            parameters=[{
                "use_sim_time": use_sim_time,
                "robot_description": robot_desc,
            }],
        ),

        # Driver principal del Go2. Este es el que publica /odom, /point_cloud2,
        # /go2_states, /joint_states, /camera/image_raw, etc.
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

        # PointCloud2 directo del driver -> LaserScan bruto.
        # Evitamos lidar_processor y open3d.
        Node(
            package="pointcloud_to_laserscan",
            executable="pointcloud_to_laserscan_node",
            name="go2_pointcloud_to_laserscan",
            output="screen",
            remappings=[
                ("cloud_in", "/point_cloud2"),
                ("scan", "/scan_raw"),
            ],
            parameters=[{
                "target_frame": "base_link",
                "transform_tolerance": 0.20,

                "min_height": -0.30,
                "max_height": 1.40,

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

        # Filtro para quitar retornos demasiado cercanos / cuerpo del robot.
        ExecuteProcess(
            cmd=[
                "python3", scan_filter_script,
                "--ros-args",
                "-p", "input_scan_topic:=/scan_raw",
                "-p", "output_scan_topic:=/scan",
                "-p", "range_min_out:=0.30",
                "-p", "range_max_out:=12.0",
                "-p", "remove_close_radius_m:=0.30",
                "-p", "filter_front_cone:=true",
                "-p", "front_cone_half_angle_rad:=0.70",
                "-p", "front_cone_distance_m:=0.60",
                "-p", "filter_rear_cone:=false",
            ],
            output="screen",
        ),

        # SLAM Toolbox.
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(
                    get_package_share_directory("slam_toolbox"),
                    "launch",
                    "online_async_launch.py",
                )
            ),
            launch_arguments={
                "slam_params_file": slam_params,
                "use_sim_time": use_sim_time,
            }.items(),
        ),

        # Bridge para Isaac: /go2_dt/base_pose, /go2_dt/feet_body, /go2_dt/rpy.
        Node(
            package="go2_dt_bridge",
            executable="state_bridge",
            name="state_bridge",
            output="screen",
        ),

        # Tus scripts antiguos publican en /cmd_vel_out; el SDK escucha /cmd_vel.
        ExecuteProcess(
            cmd=[
                "python3", cmd_vel_relay_script,
                "--ros-args",
                "-p", "input_topic:=/cmd_vel_out",
                "-p", "output_topic:=/cmd_vel",
            ],
            output="screen",
        ),

        # Visualizador propio: mapa SLAM + scan + novedad/persona.
        ExecuteProcess(
            cmd=[
                "python3", viewer_script,
                "--ros-args",
                "-p", "map_topic:=/map",
                "-p", "scan_topic:=/scan",
                "-p", "target_frame:=map",
                "-p", "robot_frame:=base_link",
                "-p", "baseline_delay_sec:=8.0",
                "-p", "novelty_required_points:=6",
                "-p", "novelty_cluster_radius_m:=0.45",
                "-p", "state_path:=/home/nextnet/AlbertoDir/go2_dt/ros2_ws/lidar_outputs/live_lidar_state.json",
            ],
            output="screen",
        ),
    ])
