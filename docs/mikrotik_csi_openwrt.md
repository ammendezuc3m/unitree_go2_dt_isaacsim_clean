# MikroTik / OpenWrt CSI and Raspi Sender

## Topology

- AP13 management IP: `192.168.1.13`
- STA12 management IP: `192.168.1.12`
- AP13 radio IP: `10.10.10.1`
- STA12 radio IP: `10.10.10.2`
- Spark demo IP: `172.16.12.170`
- Raspi demo IP: `172.16.13.100`

## Setup

```bash
cd /home/nextnet/AlbertoDir/go2_dt

RASPI_MGMT_HOST=10.39.251.226 \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh
```

## CSI live streaming on OpenWrt

Script:

```text
/root/scripts_csi_dog/stream_csi_live_05s_single.sh
```

Run:

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
PERIOD_S=0.5
```

The script triggers CSI with:

```bash
cat "$MAC_BIN" | iw dev wlan0 vendor recv 0x001374 0x93 -
```

## Dataset capture

```bash
/root/scripts_csi_dog/capture_countdown.sh person 120 100 1 10
```

It calls `/root/scripts_csi_dog/capture_csi.sh` and creates finite labelled captures.

## Raspi sender

Expected script:

```text
/home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
```

Important defaults:

```text
SPARK_IP=172.16.12.170
CAMERA_DEVICE=/dev/video0
CAMERA_PORT=6000
MP4_FILE=/home/nextnet/raspi_60ghz_demo/golden_test.mp4
MP4_PORT=6002
IPERF_PORT=5201
```

Use custom MP4:

```bash
MP4_FILE=/home/nextnet/raspi_60ghz_demo/my_video.mp4 \
ENABLE_MP4=1 \
/home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
```

If you change ports, update both sender and receiver.
