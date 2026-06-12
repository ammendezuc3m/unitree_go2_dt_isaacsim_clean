# ROS 2 Workspace: `go2_dt/ros2_ws`

This is the ROS 2 Jazzy workspace used by the demo.

It contains:

- the modified Go2 ROS 2 SDK;
- the patrol controller;
- SLAM/LiDAR processing;
- Isaac Sim sync script;
- route recording/replay files;
- live JSON outputs used by the digital twin.

---

## 1. Build

```bash
cd /home/nextnet/AlbertoDir/go2_dt/ros2_ws

source /opt/ros/jazzy/setup.bash

rosdep update
rosdep install --from-paths src --ignore-src -r -y

colcon build --symlink-install
source install/setup.bash
```

---

## 2. DDS / ROS 2 communication

Use the same DDS/domain configuration in every terminal:

```bash
export ROS_DOMAIN_ID=0
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
```

Checks:

```bash
ros2 node list
ros2 topic list
ros2 topic echo /cmd_vel_out
ros2 topic hz /cmd_vel_out
```

If only `/parameter_events` and `/rosout` appear, the nodes are not running, the workspace was not sourced, or terminals are on different DDS domains.

---

## 3. Go2 SDK

SDK path:

```text
go2_dt/ros2_ws/src/go2_ros2_sdk
```

Recommended launcher:

```bash
ros2 launch go2_robot_sdk webrtc_web.launch.py
```

Example:

```bash
cd /home/nextnet/AlbertoDir/go2_dt/ros2_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash

export ROBOT_IP=192.168.12.1
export CONN_TYPE=webrtc

ros2 launch go2_robot_sdk webrtc_web.launch.py
```

The important remap is:

```text
cmd_vel_out -> cmd_vel
```

So the movement chain is:

```text
zone_loop_patrol_v3.py
  publishes /cmd_vel_out
      ↓
go2_robot_sdk webrtc_web.launch.py
  remaps cmd_vel_out to cmd_vel
      ↓
go2_driver_node
  sends commands to Unitree Go2 through WebRTC
```

---

## 4. Patrol controller

Script:

```text
zone_loop_patrol_v3.py
```

Modes:

| Mode | Meaning |
|---|---|
| `teach` | Record route phases. |
| `auto` | Replay route phases and stop/resume using sensor JSONs. |

Important parameters:

| Parameter | Default | Meaning |
|---|---|---|
| `mode` | `teach` | `teach` or `auto`. |
| `cmd_topic` | `/cmd_vel_out` | ROS 2 Twist command topic. |
| `output_file` | `phase_trained_lap.json` | Route file written in teach mode. |
| `input_file` | `phase_trained_lap.json` | Route file read in auto mode. |
| `repeat_auto` | `false` | Repeat route automatically. |
| `camera_state_path` | camera JSON path | Camera detection state. |
| `mmwave_state_path` | CSI/mmWave JSON path | CSI/mmWave detection state. |
| `lidar_state_path` | LiDAR JSON path | LiDAR/SLAM detection state. |
| `enable_camera_stop` | `true` | Enable camera safety stop. |
| `enable_mmwave_stop` | `true` | Enable CSI/mmWave safety stop. |
| `enable_lidar_stop` | `false` | Enable LiDAR safety stop. |
| `sensor_state_max_age_sec` | `3.0` | Max age by file modification time. |
| `person_confirm_sec` | `2.0` | Stable detection required before stop. |
| `clear_confirm_sec` | `2.0` | Stable clear required before resume. |

---

## 5. Route JSON: `phase_trained_lap.json`

The route file stores a sequence of actions:

```json
{
  "version": 6,
  "type": "zone_loop_patrol_direct_camera_align",
  "created_at": 1780000000.0,
  "cmd_topic": "/cmd_vel_out",
  "forward_speed": 0.06,
  "backward_speed": 0.045,
  "strafe_speed": 0.035,
  "turn_speed": 0.16,
  "apriltag_zero": {
    "x_px": 613.37,
    "y_px": 359.63,
    "yaw_deg": -1.89
  },
  "action_count": 2,
  "total_duration_sec": 3.5,
  "actions": [
    {
      "action_type": "FORWARD",
      "duration_sec": 2.0,
      "linear_x": 0.06,
      "linear_y": 0.0,
      "angular_z": 0.0
    }
  ]
}
```

Accepted action types:

```text
FORWARD
BACKWARD
TURN_LEFT
TURN_RIGHT
STRAFE_LEFT
STRAFE_RIGHT
STOP
```

---

## 6. Isaac sync

Run inside Isaac Sim:

```python
exec(open("/workspace/go2_dt/ros2_ws/go2_dt_sync_demo2_new.py").read())
```

The script expects:

```text
/World/go2
```

It:

- prepares articulation root;
- disables collisions/gravity;
- sets joint drives;
- creates/updates LiDAR visual objects;
- subscribes to ROS 2 pose/feet/cloud data;
- reads JSON state files;
- registers an Isaac update callback.

Debug helpers:

```python
builtins.go2_print_real_pose()
builtins.go2_print_anchor()
builtins.go2_force_anchor_reset()
```
