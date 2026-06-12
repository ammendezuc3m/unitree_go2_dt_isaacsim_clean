# MikroTik / OpenWrt CSI and 60 GHz Setup

This document describes the MikroTik/OpenWrt and Raspberry Pi side of the demo.

---

## 1. Default topology

```text
Spark / main PC
  enP7s7
  172.16.12.170
        |
AP13 MikroTik/OpenWrt
  management: 192.168.1.13
  radio wlan0: 10.10.10.1/24
        |
        | 60 GHz
        |
STA12 MikroTik/OpenWrt
  management: 192.168.1.12
  radio wlan0: 10.10.10.2/24
        |
Raspberry Pi
  demo IP: 172.16.13.100
```

---

## 2. Setup AP13/STA12/Raspi

```bash
cd /home/nextnet/AlbertoDir/go2_dt

RASPI_MGMT_HOST=10.39.251.226 \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh
```

The script:

1. detects Raspi by SSH;
2. accesses AP13 and STA12;
3. checks `iw dev wlan0 link`;
4. checks AP↔STA radio ping;
5. repairs routes and neighbor tables;
6. verifies Spark↔Raspi E2E connectivity.

Manual checks:

```bash
ssh root@192.168.1.12 'iw dev wlan0 link'
ssh root@192.168.1.12 'ping -I wlan0 -c 3 10.10.10.1'
ssh root@192.168.1.13 'ping -I wlan0 -c 3 10.10.10.2'

ping -c 3 172.16.13.100
ssh nextnet@172.16.13.100 'ping -c 3 172.16.12.170'
```

---

## 3. CSI live streaming

CSI scripts live on OpenWrt:

```text
/root/scripts_csi_dog/
```

Validated live streamer:

```text
/root/scripts_csi_dog/stream_csi_live_05s_single.sh
```

Usage:

```bash
/root/scripts_csi_dog/stream_csi_live_05s_single.sh <PC_IP> <PC_USER>
```

Example:

```bash
/root/scripts_csi_dog/stream_csi_live_05s_single.sh 172.16.12.170 nextnet
```

It appends valid CSI lines to:

```text
/home/nextnet/AlbertoDir/go2_dt/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt
```

Important variables:

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

The script triggers CSI using:

```bash
cat "$MAC_BIN" | iw dev wlan0 vendor recv 0x001374 0x93 -
```

and validates `[AOA] Measurement:` lines from `dmesg`.

---

## 4. CSI dataset capture with countdown

Dataset script:

```text
/root/scripts_csi_dog/capture_countdown.sh
```

Usage:

```bash
./capture_countdown.sh <label> <num_samples> <num_runs> [start_id] [rest_seconds]
```

Example:

```bash
./capture_countdown.sh person 120 100 1 10
```

It calls:

```text
/root/scripts_csi_dog/capture_csi.sh
```

The live mode and dataset mode use the same idea, but dataset mode writes finite labelled captures instead of appending forever to the live stream.

---

## 5. Raspi sender

Expected script:

```text
/home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
```

Important defaults:

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

### Use your own MP4

```bash
scp my_video.mp4 nextnet@172.16.13.100:/home/nextnet/raspi_60ghz_demo/my_video.mp4
```

```bash
SPARK_IP=172.16.12.170 \
ENABLE_MP4=1 \
MP4_FILE=/home/nextnet/raspi_60ghz_demo/my_video.mp4 \
/home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
```

The Spark receiver listens on UDP/RTP `6002`.

---

## 6. Troubleshooting

### CSI not updating

On OpenWrt:

```bash
ps w | grep stream_csi
iw dev wlan0 link
ping -I wlan0 -c 3 10.10.10.1
tail -f /tmp/csi_05s_single_local.txt
```

On PC:

```bash
tail -f /home/nextnet/AlbertoDir/go2_dt/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt
stat /home/nextnet/AlbertoDir/go2_dt/csi_dog_dataset_20210421_181125/analysis_outputs/live_prediction_state.json
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
