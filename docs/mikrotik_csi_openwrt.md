# MikroTik / OpenWrt CSI and Raspi Sender

This document describes the MikroTik/OpenWrt and Raspberry Pi side of the demo.

The scripts described here can be executed manually on the MikroTik/OpenWrt device when debugging or collecting datasets. However, in the complete demo flow they are normally triggered indirectly through SSH by the main PC/Spark scripts. In other words, the main launcher or the Raspi/AP13/STA12 setup scripts orchestrate the radio devices remotely, but the actual CSI capture logic runs inside OpenWrt.

---

## 1. Topology

Default validated topology:

```text
Spark / main PC
  Ethernet/demo IP: 172.16.12.170
  MikroTik management network: 192.168.1.0/24
        |
        |
AP13 MikroTik/OpenWrt
  management IP: 192.168.1.13
  radio wlan0: 10.10.10.1/24
        |
        | 60 GHz wireless link
        |
STA12 MikroTik/OpenWrt
  management IP: 192.168.1.12
  radio wlan0: 10.10.10.2/24
        |
        |
Raspberry Pi
  demo IP: 172.16.13.100
```

Important addresses:

```text
AP13 management IP: 192.168.1.13
STA12 management IP: 192.168.1.12
AP13 radio IP:      10.10.10.1
STA12 radio IP:     10.10.10.2
Spark demo IP:      172.16.12.170
Raspi demo IP:      172.16.13.100
```

---

## 2. Repository deployment scripts

The repository contains the source-of-truth runtime files for the external devices:

```text
deployment/
├── openwrt/
│   └── scripts_csi_dog/
│       ├── stream_csi_live_05s_single.sh
│       ├── capture_countdown.sh
│       └── capture_csi.sh
└── raspi/
    └── raspi_60ghz_demo/
        ├── raspi_60ghz_sender.sh
        ├── iperf_client_loop.sh
        └── mp4_loop_tx_6002.sh
```

The validated MP4 used by the Raspberry Pi demo is:

```text
go2_dt/media/golden_test.mp4
```

It is stored with Git LFS, so a fresh host clone must run `git lfs pull` before provisioning.

For the current AP13/STA12/Raspi architecture, **manual SCP is not the normal procedure**. The canonical setup script copies the tracked files automatically:

```text
PC/Spark repository
  ├─ deployment/raspi/.../*.sh
  │      -> Raspberry Pi /home/system/raspi_60ghz_demo/
  ├─ go2_dt/media/golden_test.mp4
  │      -> Raspberry Pi /home/nextnet/raspi_60ghz_demo/golden_test.mp4
  └─ deployment/openwrt/scripts_csi_dog/*.sh
         -> Raspberry Pi SSH hop -> STA12 /root/scripts_csi_dog/
```

AP13 does not receive the CSI scripts because it does not execute CSI capture. Its `hostapd` configuration is created remotely by the setup.

Manual copying is still documented in `deployment/README.md` for recovery/debugging, but it is not required when the canonical setup succeeds.

---

## 3. Setup from the main PC/Spark

The standard way to configure and verify the AP13/STA12/Raspi path is to run the setup script from the PC/Spark:

```bash
cd /home/nextnet/AlbertoDir/go2_dt

RASPI_MGMT_HOST=10.39.251.226 \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh
```

This script performs the remote setup through SSH. In the current repository it:

- checks access to the Raspberry Pi;
- provisions the Raspberry Pi sender, MP4 loop and iperf loop;
- copies the materialized `golden_test.mp4` to the Raspberry Pi;
- reaches STA12 through the Raspberry Pi SSH hop and provisions all tracked CSI scripts;
- configures AP13 and STA12 radio settings;
- checks the AP13–STA12 wireless association and radio ping over `wlan0`;
- configures/repairs Spark, radio and Raspberry Pi routes;
- verifies end-to-end Spark↔Raspberry Pi reachability.

Before running it on a fresh clone:

```bash
cd /home/nextnet/AlbertoDir
git lfs pull
bash requirements/check_lfs_assets.sh
```

Manual checks:

```bash
ssh root@192.168.1.13 'echo AP_OK'
ssh root@192.168.1.12 'echo STA_OK'

ssh root@192.168.1.12 'iw dev wlan0 link'
ssh root@192.168.1.12 'ping -I wlan0 -c 3 10.10.10.1'
ssh root@192.168.1.13 'ping -I wlan0 -c 3 10.10.10.2'

ping -c 3 172.16.13.100
ssh nextnet@172.16.13.100 'ping -c 3 172.16.12.170'
```

---

## 4. CSI live streaming on OpenWrt

### 4.1 Where it runs

The CSI measurement script runs **inside the MikroTik/OpenWrt device**, normally on the STA side.

Expected script path after deployment:

```text
/root/scripts_csi_dog/stream_csi_live_05s_single.sh
```

Manual execution:

```bash
/root/scripts_csi_dog/stream_csi_live_05s_single.sh 172.16.12.170 nextnet
```

In the full flow, this script may be started remotely through SSH from the PC/Spark pipeline.

### 4.2 What it writes on the PC/Spark

The OpenWrt script appends valid CSI measurements to the PC/Spark file:

```text
/home/nextnet/AlbertoDir/go2_dt/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt
```

This file is then consumed by the PC-side predictor, which writes:

```text
/home/nextnet/AlbertoDir/go2_dt/csi_dog_dataset_20210421_181125/analysis_outputs/live_prediction_state.json
```

That JSON is consumed by:

- `zone_loop_patrol_v3.py` for safety-stop decisions;
- `go2_dt_sync_demo2_new.py` for visual state in Isaac Sim.

### 4.3 Important variables inside the OpenWrt script

```text
PC_FILE=/home/nextnet/AlbertoDir/go2_dt/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt
KEY=/root/.ssh/id_rsa_dropbear
MAC_BIN=/tmp/ap_mac.bin
AP_IP=10.10.10.1
AP_MAC_TEXT=b8:69:f4:d5:49:a9
LOCAL_LOG=/tmp/csi_05s_single_local.txt
LOCKDIR=/tmp/csi_05s_single.lock
PERIOD_S=0.5
AFTER_TRIGGER_SLEEP_S=0.2
```

If you change the project root on the PC/Spark, you must also update `PC_FILE` inside the OpenWrt script. Otherwise the CSI stream will still be appended to the old path and the predictor will not see new data.

### 4.4 How the CSI trigger works

The live streamer triggers one CSI measurement using the vendor command:

```bash
cat "$MAC_BIN" | iw dev wlan0 vendor recv 0x001374 0x93 -
```

Then it reads the latest `[AOA] Measurement:` line from `dmesg`, validates it, and sends it to the PC through SSH.

---

## 5. Dataset capture with countdown

### 5.1 Where it runs

The countdown capture script also runs directly on the MikroTik/OpenWrt device:

```text
/root/scripts_csi_dog/capture_countdown.sh
```

This is mainly used for dataset collection rather than live demo execution.

### 5.2 Usage

```bash
/root/scripts_csi_dog/capture_countdown.sh <label> <num_samples> <num_runs> [start_id] [rest_seconds]
```

Example:

```bash
/root/scripts_csi_dog/capture_countdown.sh person 120 100 1 10
```

It calls:

```text
/root/scripts_csi_dog/capture_csi.sh
```

---

## 6. Raspi sender

### 6.1 Where it runs

The Raspi sender runs on the Raspberry Pi:

```text
/home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
```

In the full pipeline, the PC/Spark scripts start it remotely through SSH. You can also run it manually on the Raspi for debugging.

### 6.2 Important defaults

```text
SPARK_IP=172.16.12.170

CAMERA_DEVICE=/dev/video0
CAMERA_WIDTH=424
CAMERA_HEIGHT=240
CAMERA_FPS=15
CAMERA_BITRATE_KBPS=2000
CAMERA_PORT=6000

MP4_FILE=/home/nextnet/raspi_60ghz_demo/golden_test.mp4
MP4_WIDTH=1280
MP4_HEIGHT=720
MP4_FPS=15
MP4_BITRATE_KBPS=20000
MP4_PORT=6002

ENABLE_MP4=0
ENABLE_CAMERA=1
ENABLE_IPERF=1

IPERF_PORT=5201
IPERF_BITRATE=400M
IPERF_MODE=tcp
IPERF_DURATION=3600
```

### 6.3 Use your own MP4

Copy the MP4 to the Raspi:

```bash
scp my_video.mp4 nextnet@172.16.13.100:/home/nextnet/raspi_60ghz_demo/my_video.mp4
```

Run the sender with:

```bash
SPARK_IP=172.16.12.170 \
ENABLE_MP4=1 \
MP4_FILE=/home/nextnet/raspi_60ghz_demo/my_video.mp4 \
/home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
```

If only `MP4_FILE` changes, the PC/Spark receiver does not need to change because it still listens on UDP/RTP port `6002`.

If you change `MP4_PORT`, update both:

- Raspi sender `MP4_PORT`;
- Spark receiver port in the pipeline script.

---

## 7. Full Raspi pipeline from Spark

From the PC/Spark:

```bash
cd /home/nextnet/AlbertoDir/go2_dt

RASPI_HOST=172.16.13.100 \
RASPI_USER=nextnet \
SPARK_IP=172.16.12.170 \
ENABLE_CAMERA=1 \
ENABLE_MP4=1 \
ENABLE_IPERF=1 \
ENABLE_CSI=0 \
./tests_experiments/raspi_60ghz/run_raspi_60ghz_pipeline.sh
```

This launches Spark-side receivers and starts the Raspi sender remotely.

---

## 8. Troubleshooting

### Radio not associated

```bash
ssh root@192.168.1.12 'iw dev wlan0 link'
```

### Spark cannot ping Raspi

```bash
ip route get 172.16.13.100
ping -c 3 172.16.13.100
```

### MP4 not working

On Raspi:

```bash
ls -lh /home/nextnet/raspi_60ghz_demo/*.mp4
tail -f /home/nextnet/raspi_60ghz_demo/logs/mp4_tx.log
```

On Spark:

```bash
sudo tcpdump -i enP7s7 udp port 6002
```

### CSI not updating

On OpenWrt:

```bash
ps w | grep stream_csi
iw dev wlan0 link
ping -I wlan0 -c 3 10.10.10.1
tail -f /tmp/csi_05s_single_local.txt
```

On PC/Spark:

```bash
tail -f /home/nextnet/AlbertoDir/go2_dt/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt
stat /home/nextnet/AlbertoDir/go2_dt/csi_dog_dataset_20210421_181125/analysis_outputs/live_prediction_state.json
```
