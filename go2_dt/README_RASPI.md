# Raspberry Pi / AP13 / STA12 demo

This document is the compact Raspberry Pi-specific entry point. The canonical installation and provisioning details live in:

- [../README.md](../README.md) for fresh installation and full-demo execution.
- [../deployment/README.md](../deployment/README.md) for Raspberry Pi and MikroTik provisioning.
- [../docs/mikrotik_csi_openwrt.md](../docs/mikrotik_csi_openwrt.md) for radio/CSI details.

All host-side paths are resolved from the repository itself. The Git repository can be cloned in any directory.

## 1. Prepare the host

From the repository root:

```bash
git lfs install
git lfs pull
bash requirements/check_lfs_assets.sh
bash requirements/setup_host_env.sh
```

Build the ROS 2 workspace:

```bash
source /opt/ros/jazzy/setup.bash
source .venv/bin/activate

cd go2_dt/ros2_ws
rosdep update
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash

cd ../..
```

## 2. Physical topology

Validated topology:

```text
PC/Spark -- Ethernet --> AP13 -- 60 GHz --> STA12 -- Ethernet --> Raspberry Pi

PC/Spark demo IP: 172.16.12.170
AP13 management:  192.168.1.13
AP13 radio:       10.10.10.1
STA12 management: 192.168.1.12
STA12 radio:      10.10.10.2
Raspberry Pi:     172.16.13.100
```

## 3. Provision Raspberry Pi and STA12

From the repository root:

```bash
cd go2_dt

RASPI_MGMT_HOST=<raspi-management-ip> \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh
```

The setup automatically:

1. detects/reaches the Raspberry Pi;
2. copies the tracked Raspberry Pi sender scripts;
3. copies the materialized `go2_dt/media/golden_test.mp4`;
4. reaches STA12 through the Raspberry Pi SSH hop;
5. copies the tracked CSI scripts to STA12;
6. configures AP13/STA12 radio settings and routes;
7. verifies radio and end-to-end connectivity.

No manual host-path editing is required.

## 4. Files installed remotely

Raspberry Pi:

```text
/home/system/raspi_60ghz_demo/
├── raspi_60ghz_sender.sh
├── iperf_client_loop.sh
└── mp4_loop_tx_6002.sh

~/raspi_60ghz_demo/
└── golden_test.mp4
```

STA12:

```text
/root/scripts_csi_dog/
├── stream_csi_live_05s_single.sh
├── capture_csi.sh
└── capture_countdown.sh
```

These are remote-device runtime locations and are unrelated to the directory where the Git repository is cloned on the PC.

## 5. Run the Raspberry Pi pipeline

From `go2_dt/`:

```bash
RASPI_HOST=172.16.13.100 \
RASPI_USER=nextnet \
SPARK_IP=172.16.12.170 \
STA_MGMT_HOST=192.168.1.12 \
ENABLE_CAMERA=1 \
ENABLE_MP4=1 \
ENABLE_IPERF=1 \
./tests_experiments/raspi_60ghz_ap13_sta12/run_ap13_sta12_pipeline.sh
```

For the complete Isaac + Go2 + sensing stack, use:

```bash
./run_full_demo_ap13_sta12_raspi.sh
```

## 6. Use a different MP4

Copy it to the Raspberry Pi user's home:

```bash
scp my_video.mp4 nextnet@172.16.13.100:~/raspi_60ghz_demo/my_video.mp4
```

Then on the Raspberry Pi:

```bash
MP4_FILE="$HOME/raspi_60ghz_demo/my_video.mp4" \
ENABLE_MP4=1 \
/home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
```

## 7. Stop

From `go2_dt/`:

```bash
./tests_experiments/raspi_60ghz_ap13_sta12/stop_ap13_sta12_pipeline.sh
```

or for the full stack:

```bash
./stop_full_demo_ap13_sta12_raspi_clean.sh
```
