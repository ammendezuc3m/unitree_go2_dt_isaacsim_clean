# Scripts Overview

This document classifies the most important scripts.

---

## 1. Official scripts

| Script | Classification | Description |
|---|---|---|
| `go2_dt/run_full_demo_current.sh` | Official full launcher | Starts Isaac, ROS/Go2, SLAM, radio/video/CSI/YOLO, throughput and patrol. |
| `go2_dt/stop_full_demo_current.sh` | Official stopper | Cleans local and remote demo processes. |
| `go2_dt/ros2_ws/zone_loop_patrol_v3.py` | Official patrol | Teach/auto route control and safety stop. |
| `go2_dt/ros2_ws/go2_dt_sync_demo2_new.py` | Official Isaac sync | Runs inside Isaac Sim and updates the digital twin. |
| `go2_dt/tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh` | Official radio setup | Sets AP13/STA12/Raspi connectivity. |
| `go2_dt/tests_experiments/raspi_60ghz_ap13_sta12/run_ap13_sta12_pipeline.sh` | Official Raspi wrapper | Runs Raspi pipeline with AP13/STA12 defaults. |
| `go2_dt/tests_experiments/raspi_60ghz/run_raspi_60ghz_pipeline.sh` | Official Raspi pipeline | Spark receivers + Raspi sender. |
| `go2_dt/csi_live_predictor_from_stream.py` | Official CSI predictor | Reads CSI stream and writes prediction JSON. |

---

## 2. Validate scripts

Bash:

```bash
bash -n script.sh
```

Python:

```bash
python3 -m py_compile script.py
```

ROS:

```bash
ros2 node list
ros2 topic list
ros2 topic echo /cmd_vel_out
```

---

## 3. Legacy and ignored scripts

Do not document or commit as official execution paths:

```text
_quarantine*/
_archive_unused*/
build/
install/
log/
venv/
.venv*/
site-packages/
```
