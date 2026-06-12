# go2_dt

`go2_dt/` contains the operational demo code: ROS 2 workspace, Go2 SDK integration, patrol controller, CSI predictor, video/camera receivers, 60 GHz scripts and full-demo launchers.

---

## 1. Main scripts

| Script | Purpose |
|---|---|
| `run_full_demo_current.sh` | Full launcher for Isaac Sim, Go2 SLAM, 60 GHz video/CSI/YOLO/iperf stack and patrol. |
| `stop_full_demo_current.sh` | Stops local processes, ROS/Go2, Isaac Docker and remote AP/STA processes. |
| `demo_60ghz_video_iperf_csi_yolo_usb.sh` | PC-local radio/video/CSI/YOLO stack using AP13/STA12 and local USB YOLO. |
| `csi_live_predictor_from_stream.py` | Reads CSI stream text and writes `live_prediction_state.json`. |
| `video_file_tx_rx_yolo_usb.py` | Video/YOLO helper for local tests. |
| `ros2_ws/zone_loop_patrol_v3.py` | Patrol teach/auto controller with safety stops. |
| `ros2_ws/go2_dt_sync_demo2_new.py` | Isaac Sim sync script. |
| `tests_experiments/raspi_60ghz/run_raspi_60ghz_pipeline.sh` | Raspi sender + Spark receivers. |
| `tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh` | Robust AP13/STA12/Raspi route setup. |
| `tests_experiments/raspi_60ghz_ap13_sta12/run_ap13_sta12_pipeline.sh` | Validated Raspi/AP13/STA12 mode wrapper. |

---

## 2. Full demo launcher

```bash
cd /home/nextnet/AlbertoDir/go2_dt
./run_full_demo_current.sh
```

This script launches:

1. Isaac Sim Docker.
2. Go2 SDK + SLAM live visual.
3. 60 GHz radio/video/CSI/YOLO/iperf stack.
4. Throughput plotting.
5. `zone_loop_patrol_v3.py` in auto mode.

Default important values:

```text
ROBOT_IP=192.168.12.1
CONN_TYPE=webrtc
LOCAL_IFACE=enP7s7
LOCAL_VIDEO_IP=192.168.1.170/24
AP_HOST=192.168.1.13
STA_HOST=192.168.1.12
ZONE_LOOP_CMD_TOPIC=/cmd_vel_out
ZONE_LOOP_REPEAT_AUTO=true
ZONE_LOOP_ENABLE_CAMERA_STOP=true
ZONE_LOOP_ENABLE_MMWAVE_STOP=true
ZONE_LOOP_ENABLE_LIDAR_STOP=true
```

Stop:

```bash
./stop_full_demo_current.sh
```

---

## 3. Execute modules independently

### Go2 SDK

```bash
cd /home/nextnet/AlbertoDir/go2_dt/ros2_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash

export ROBOT_IP=192.168.12.1
export CONN_TYPE=webrtc

ros2 launch go2_robot_sdk webrtc_web.launch.py
```

### Patrol teach mode

```bash
cd /home/nextnet/AlbertoDir/go2_dt/ros2_ws
source /opt/ros/jazzy/setup.bash
source install/setup.bash

python3 zone_loop_patrol_v3.py \
  --ros-args \
  -p mode:=teach \
  -p cmd_topic:=/cmd_vel_out \
  -p output_file:=/home/nextnet/AlbertoDir/go2_dt/ros2_ws/phase_trained_lap.json \
  -p apriltag_camera_device:=/dev/video0 \
  -p apriltag_id:=0 \
  -p show_camera_window:=true
```

Controls:

```text
C = set current AprilTag pose as zero
W = forward
S = backward
A = turn left
D = turn right
Q = strafe left
E = strafe right
X = stop phase
G = save recording
```

### Patrol auto mode

```bash
python3 zone_loop_patrol_v3.py \
  --ros-args \
  -p mode:=auto \
  -p cmd_topic:=/cmd_vel_out \
  -p input_file:=/home/nextnet/AlbertoDir/go2_dt/ros2_ws/phase_trained_lap.json \
  -p repeat_auto:=true \
  -p enable_camera_stop:=true \
  -p enable_mmwave_stop:=true \
  -p enable_lidar_stop:=true \
  -p person_confirm_sec:=0.5 \
  -p clear_confirm_sec:=1.5
```

### Isaac sync

Inside Isaac Sim after opening the USD scene:

```python
exec(open("/workspace/go2_dt/ros2_ws/go2_dt_sync_demo2_new.py").read())
```

---

## 4. Raspi/AP13/STA12 pipeline

Setup:

```bash
cd /home/nextnet/AlbertoDir/go2_dt

RASPI_MGMT_HOST=10.39.251.226 \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh
```

Run:

```bash
RASPI_HOST=172.16.13.100 \
SPARK_IP=172.16.12.170 \
STA_MGMT_HOST=192.168.1.12 \
ENABLE_CAMERA=1 \
ENABLE_MP4=1 \
ENABLE_IPERF=1 \
ENABLE_CSI=0 \
./tests_experiments/raspi_60ghz_ap13_sta12/run_ap13_sta12_pipeline.sh
```

---

## 5. Custom MP4 files

### Raspi architecture

The sender uses:

```text
MP4_FILE=/home/nextnet/raspi_60ghz_demo/golden_test.mp4
```

Override it:

```bash
MP4_FILE=/home/nextnet/raspi_60ghz_demo/my_video.mp4 \
ENABLE_MP4=1 \
/home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
```

### PC/local architecture

Look for `VIDEO_FILE` in the local script and set it to your file:

```text
VIDEO_FILE=/home/nextnet/AlbertoDir/go2_dt/media/my_video.mp4
```

---

## 6. Hardcoded paths

Several scripts were validated with:

```text
/home/nextnet/AlbertoDir/go2_dt
```

For a different clone location, either edit the scripts or create a symlink:

```bash
mkdir -p /home/nextnet/AlbertoDir
ln -s /home/nextnet/unitree_go2_dt_isaacsim_clean/go2_dt /home/nextnet/AlbertoDir/go2_dt
```
