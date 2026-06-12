# Deployment scripts

This folder contains the scripts that must be copied to the external devices used by the Raspi/MikroTik architecture.

The main repository code runs on the **PC/Spark**. The scripts in this folder are installed on the target devices and are then executed either manually during debugging or remotely through SSH by the Spark-side pipeline.

---

## 1. Folder structure

```text
deployment/
├── README.md
├── openwrt/
│   └── scripts_csi_dog/
│       ├── stream_csi_live_05s_single.sh
│       ├── capture_countdown.sh
│       └── capture_csi.sh
└── raspi/
    └── raspi_60ghz_demo/
        ├── raspi_60ghz_sender.sh
        └── iperf_client_loop.sh
```

---

## 2. OpenWrt / MikroTik scripts

Repository path:

```text
deployment/openwrt/scripts_csi_dog/
```

Target path on the MikroTik/OpenWrt device:

```text
/root/scripts_csi_dog/
```

These scripts are intended to run inside the MikroTik/OpenWrt device, normally on the STA side. In the complete demo, they can be started remotely from the PC/Spark through SSH.

### 2.1 `stream_csi_live_05s_single.sh`

Purpose:

```text
Live CSI streaming from OpenWrt to the PC/Spark.
```

It triggers CSI measurements with the OpenWrt/MikroTik vendor command, validates the measurement lines from `dmesg`, and appends valid CSI lines to the PC/Spark file:

```text
/home/nextnet/AlbertoDir/go2_dt/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt
```

Manual usage on OpenWrt:

```bash
/root/scripts_csi_dog/stream_csi_live_05s_single.sh 172.16.12.170 nextnet
```

Arguments:

```text
172.16.12.170 = PC/Spark IP that receives CSI
nextnet       = PC/Spark Linux user
```

### 2.2 `capture_countdown.sh`

Purpose:

```text
Dataset capture helper with countdown and rest time between captures.
```

Manual usage on OpenWrt:

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

Therefore, `capture_csi.sh` must also be installed on the OpenWrt device.

### 2.3 `capture_csi.sh`

Purpose:

```text
Low-level finite CSI capture script called by capture_countdown.sh.
```

If this file is missing, `capture_countdown.sh` will not be able to collect labelled datasets.

---

## 3. Install OpenWrt / MikroTik scripts

From the PC/Spark:

```bash
cd /home/nextnet/AlbertoDir

scp -r deployment/openwrt/scripts_csi_dog root@192.168.1.12:/root/

ssh root@192.168.1.12 'chmod +x /root/scripts_csi_dog/*.sh'
```

Use `192.168.1.12` for STA12.

If the CSI scripts must run on AP13 instead, use:

```bash
scp -r deployment/openwrt/scripts_csi_dog root@192.168.1.13:/root/

ssh root@192.168.1.13 'chmod +x /root/scripts_csi_dog/*.sh'
```

Verify installation:

```bash
ssh root@192.168.1.12 'ls -lh /root/scripts_csi_dog && ls -lh /root/scripts_csi_dog/*.sh'
```

---

## 4. Raspberry Pi scripts

Repository path:

```text
deployment/raspi/raspi_60ghz_demo/
```

Target path on the Raspberry Pi:

```text
/home/system/raspi_60ghz_demo/
```

These scripts are intended to run on the Raspberry Pi. In the full pipeline, the Spark-side scripts start them remotely through SSH.

### 4.1 `raspi_60ghz_sender.sh`

Purpose:

```text
Starts the Raspberry Pi camera stream, optional MP4 stream and iperf client toward the PC/Spark.
```

Main default ports:

```text
Camera RTP: UDP 6000
MP4 RTP:    UDP 6002
iperf3:     TCP/UDP 5201
```

Important variables:

```text
SPARK_IP=172.16.12.170
CAMERA_DEVICE=/dev/video0
MP4_FILE=/home/nextnet/raspi_60ghz_demo/golden_test.mp4
ENABLE_CAMERA=1
ENABLE_MP4=0
ENABLE_IPERF=1
```

Manual example on the Raspi:

```bash
SPARK_IP=172.16.12.170 \
ENABLE_CAMERA=1 \
ENABLE_MP4=1 \
MP4_FILE=/home/nextnet/raspi_60ghz_demo/my_video.mp4 \
/home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
```

### 4.2 `iperf_client_loop.sh`

Purpose:

```text
Persistent iperf client loop used by raspi_60ghz_sender.sh.
```

It restarts `iperf3` automatically if the connection drops.

---

## 5. Install Raspberry Pi scripts

From the PC/Spark:

```bash
cd /home/nextnet/AlbertoDir

ssh nextnet@172.16.13.100 'sudo mkdir -p /home/system/raspi_60ghz_demo && sudo chown -R nextnet:nextnet /home/system/raspi_60ghz_demo'

scp deployment/raspi/raspi_60ghz_demo/*.sh \
  nextnet@172.16.13.100:/home/system/raspi_60ghz_demo/

ssh nextnet@172.16.13.100 'chmod +x /home/system/raspi_60ghz_demo/*.sh'
```

Verify installation:

```bash
ssh nextnet@172.16.13.100 'ls -lh /home/system/raspi_60ghz_demo'
```

---

## 6. Relationship with the main pipeline

Once the scripts have been copied to the external devices:

1. The PC/Spark can run the AP13/STA12/Raspi setup script:

```bash
cd /home/nextnet/AlbertoDir/go2_dt

RASPI_MGMT_HOST=10.39.251.226 \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh
```

2. The PC/Spark can run the Raspi/AP13/STA12 pipeline:

```bash
RASPI_HOST=172.16.13.100 \
SPARK_IP=172.16.12.170 \
STA_MGMT_HOST=192.168.1.12 \
ENABLE_CAMERA=1 \
ENABLE_MP4=1 \
ENABLE_IPERF=1 \
./tests_experiments/raspi_60ghz_ap13_sta12/run_ap13_sta12_pipeline.sh
```

The Spark-side scripts then use SSH to start or interact with the scripts installed under:

```text
/root/scripts_csi_dog/
/home/system/raspi_60ghz_demo/
```
