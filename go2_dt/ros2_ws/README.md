# ROS 2 Workspace

This workspace contains the modified Go2 ROS 2 SDK, patrol controller, SLAM/LiDAR processing and Isaac sync script.

## Build

Create the canonical project venv once from the repository root:

```bash
cd <repo-root>
bash requirements/setup_host_env.sh
```

Then build this workspace with ROS 2 and the same venv active:

```bash
source /opt/ros/jazzy/setup.bash
source <repo-root>/.venv/bin/activate

cd <repo-root>/go2_dt/ros2_ws

sudo rosdep init 2>/dev/null || true
rosdep update
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
```

Do not build the current workspace against an unrelated Python venv. The full-demo launchers resolve `<repo-root>/.venv` relative to the repository location.

## Official SLAM launch

The supported SLAM/LiDAR launch file for the current demo is:

```text
launch/go2_slam_live_visual.launch.py
```

It starts the Go2 driver, PointCloud2-to-LaserScan conversion, scan filtering, SLAM Toolbox, the Isaac state bridge, the `/cmd_vel_out -> /cmd_vel` relay and the live SLAM/LiDAR detector.

Run it with:

```bash
cd <repo-root>/go2_dt/ros2_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash
export ROBOT_IP=192.168.12.1
export CONN_TYPE=webrtc
ros2 launch ./launch/go2_slam_live_visual.launch.py
```

Only this launch file is part of the documented current path.

## DDS

Use the same configuration in all terminals:

```bash
export ROS_DOMAIN_ID=0
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
```

Checks:

```bash
ros2 node list
ros2 topic list
ros2 topic echo /cmd_vel_out
```

## Go2 SDK

```bash
export ROBOT_IP=192.168.12.1
export CONN_TYPE=webrtc
ros2 launch go2_robot_sdk webrtc_web.launch.py
```

The SDK remaps `cmd_vel_out -> cmd_vel`, so the patrol publishes to `/cmd_vel_out`.

## Patrol teach

```bash
python3 zone_loop_patrol_v3.py --ros-args \
  -p mode:=teach \
  -p cmd_topic:=/cmd_vel_out \
  -p output_file:=<repo-root>/go2_dt/ros2_ws/phase_trained_lap.json \
  -p apriltag_camera_device:=/dev/video0 \
  -p apriltag_id:=0
```

Controls: `C` set zero, `W/S/A/D/Q/E` movement phases, `X` stop phase, `G` save.

## Patrol auto

```bash
python3 zone_loop_patrol_v3.py --ros-args \
  -p mode:=auto \
  -p cmd_topic:=/cmd_vel_out \
  -p input_file:=<repo-root>/go2_dt/ros2_ws/phase_trained_lap.json \
  -p repeat_auto:=true \
  -p enable_camera_stop:=true \
  -p enable_mmwave_stop:=true \
  -p enable_lidar_stop:=true
```

## Isaac sync

Open Isaac Sim from the project launcher. The launcher mounts the repository root at `/workspace` inside the container. This is a container-internal path and does not constrain where the repository lives on the host. Then optionally run:

```python
exec(open("/workspace/go2_dt/ros2_ws/go2_dt_sync_demo2_new.py").read())
```

The sync reads JSON files and updates the Isaac digital twin. See `../../docs/json_contracts.md`.
