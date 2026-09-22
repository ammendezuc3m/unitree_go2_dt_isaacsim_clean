# go2_dt

This folder contains the operational demo scripts.

Before running any official launcher on a clean machine, create the repository-level host environment and build the ROS 2 workspace as described in [`../requirements/README.md`](../requirements/README.md). The launchers expect `../.venv` to exist.

## Official scripts

| Script | Use |
|---|---|
| `run_full_demo_current.sh` | Current/no-Raspi full launcher. |
| `stop_full_demo_current.sh` | Global stopper. |
| `ros2_ws/zone_loop_patrol_v3.py` | Teach/auto patrol and safety stop. |
| `ros2_ws/go2_dt_sync_demo2_new.py` | Isaac Sim live sync. |
| `csi_live_predictor_from_stream.py` | CSI stream to JSON predictor. |
| `tests_experiments/raspi_60ghz/run_raspi_60ghz_pipeline.sh` | Spark receivers + Raspi sender. |
| `tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh` | AP13/STA12/Raspi setup. |
| `tests_experiments/raspi_60ghz_ap13_sta12/run_ap13_sta12_pipeline.sh` | Raspi/AP13/STA12 run wrapper. |

## Current/no-Raspi run

```bash
cd go2_dt
./run_full_demo_current.sh
```

Stop:

```bash
./stop_full_demo_current.sh
```

## Raspi run

```bash
cd go2_dt

RASPI_MGMT_HOST=10.39.251.226 \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh

RASPI_HOST=172.16.13.100 \
SPARK_IP=172.16.12.170 \
STA_MGMT_HOST=192.168.1.12 \
ENABLE_CAMERA=1 \
ENABLE_MP4=1 \
ENABLE_IPERF=1 \
./tests_experiments/raspi_60ghz_ap13_sta12/run_ap13_sta12_pipeline.sh
```

## Running modules separately

See:

- `ros2_ws/README.md` for Go2 SDK, patrol and Isaac sync.
- `camera_yolo/README.md` for camera JSON.
- `csi_dog_dataset_20210421_181125/README.md` for CSI.
- `../docs/json_contracts.md` for all JSON contracts.
