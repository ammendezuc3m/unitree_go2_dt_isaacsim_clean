# Scripts Overview

| Script | Classification | Description |
|---|---|---|
| `go2_dt/run_full_demo_current.sh` | Official | Current/no-Raspi full launcher. |
| `go2_dt/stop_full_demo_current.sh` | Official | Stops local and remote processes. |
| `go2_dt/ros2_ws/zone_loop_patrol_v3.py` | Official | Teach/auto patrol and safety stop. |
| `go2_dt/ros2_ws/go2_dt_sync_demo2_new.py` | Official | Isaac Sim live sync. |
| `go2_dt/tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh` | Official | AP13/STA12/Raspi setup. |
| `go2_dt/tests_experiments/raspi_60ghz_ap13_sta12/run_ap13_sta12_pipeline.sh` | Official | Raspi/AP13/STA12 run wrapper. |
| `go2_dt/tests_experiments/raspi_60ghz/run_raspi_60ghz_pipeline.sh` | Official | Spark receivers + Raspi sender. |
| `go2_dt/csi_live_predictor_from_stream.py` | Official | CSI stream to prediction JSON. |

Do not commit/document as official paths: `_quarantine*/`, `_archive_unused*/`, `build/`, `install/`, `log/`, `venv/`, `.venv*/`, `site-packages/`.


## Legacy code intentionally retained

One historical-looking file is still connected to the current tree and therefore is **not** deleted blindly:

- `go2_dt/ros2_ws/src/go2_rect_motion/go2_rect_motion/zone_loop_patrol.py`: still referenced by the ROS package console entry point `zone_loop_patrol = go2_rect_motion.zone_loop_patrol:main`. The documented/full-demo patrol remains `go2_dt/ros2_ws/zone_loop_patrol_v3.py`.

The obsolete `zone_loop_patrol_v2*`, `zone_loop_patrol_5g.py`, SDK `*_bk.py` backups and the timestamped Raspi setup base have been removed from the clean branch. The AP13/STA12/Raspi topology configuration, provisioning and recovery logic now live in a single canonical setup script.
