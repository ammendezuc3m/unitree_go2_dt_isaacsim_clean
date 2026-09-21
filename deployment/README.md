# Deployment scripts

This folder contains the files that are executed on the external devices. They are **not host-side scripts**: the PC/Spark orchestrates the demo over SSH, but the CSI vendor command itself is executed on the MikroTik/OpenWrt device and the video sender is executed on the Raspberry Pi.

## 1. Execution model

For the MikroTik/OpenWrt CSI path the flow is:

```text
PC/Spark
  |
  | SSH: start/manage remote script
  v
MikroTik/OpenWrt STA12
  /root/scripts_csi_dog/stream_csi_live_05s_single.sh
  |
  | executes locally on wlan0:
  |   iw dev wlan0 vendor recv 0x001374 0x93 -
  v
kernel dmesg -> CSI/AoA measurement
  |
  | SSH append stream
  v
PC/Spark
  go2_dt/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt
  |
  v
csi_live_predictor_from_stream.py
```

The important distinction is that commands such as:

```bash
wpa_supplicant -D nl80211 -i wlan0 -c /etc/wpa_supplicant.conf -B
echo -n -e '\x48\x8f\x5a\xdf\x02\x3b' | iw dev wlan0 vendor recv 0x001374 0x93 -
```

must run **inside OpenWrt on the MikroTik**, because `wlan0`, the wil6210 driver and the vendor CSI command exist there. The PC only configures/starts the remote process and consumes the resulting CSI data.

## 2. Files that belong on the MikroTik

Repository source of truth:

```text
deployment/openwrt/scripts_csi_dog/
├── stream_csi_live_05s_single.sh
├── capture_csi.sh
└── capture_countdown.sh
```

Runtime destination on STA12:

```text
/root/scripts_csi_dog/
```

The three tracked scripts have different roles:

- `stream_csi_live_05s_single.sh`: official live-demo CSI streamer. It triggers CSI on the MikroTik and streams valid measurements to the PC/Spark.
- `capture_csi.sh`: finite labelled-dataset capture. It reconnects the STA with `wpa_supplicant`, triggers a requested number of CSI measurements and stores them under `/tmp/csi_dog_dataset/`.
- `capture_countdown.sh`: convenience wrapper for repeated labelled captures; it calls `/root/scripts_csi_dog/capture_csi.sh`.

The historical ZIP used during development also contains older one-off/export/SSH variants. They are intentionally **not part of the official runtime path** unless a later experiment explicitly needs them.

## 3. First-time MikroTik provisioning

Do this once on a new/reflashed MikroTik, or whenever you need to restore the deployment files.

### 3.1 Connect the PC directly to the management network

Connect the PC/Spark by Ethernet to the MikroTik management side. For the validated lab addressing:

```text
STA12 management IP: 192.168.1.12
AP13 management IP:  192.168.1.13
PC/Spark management IP used by the demo: 192.168.1.170/24
```

Example on the PC/Spark, replacing the interface if needed:

```bash
sudo ip addr replace 192.168.1.170/24 dev enP7s7
ping -c 3 192.168.1.12
ssh -o HostKeyAlgorithms=+ssh-rsa root@192.168.1.12
```

Do not continue until SSH access to the target MikroTik works.

### 3.2 Copy the tracked scripts to STA12

Run this from the **repository root** on the PC/Spark:

```bash
scp -O -o HostKeyAlgorithms=+ssh-rsa \
  deployment/openwrt/scripts_csi_dog/*.sh \
  root@192.168.1.12:/root/scripts_csi_dog/
```

If the destination directory does not exist yet:

```bash
ssh -o HostKeyAlgorithms=+ssh-rsa root@192.168.1.12 \
  'mkdir -p /root/scripts_csi_dog'
```

then repeat the `scp`.

Make them executable and verify:

```bash
ssh -o HostKeyAlgorithms=+ssh-rsa root@192.168.1.12 \
  'chmod +x /root/scripts_csi_dog/*.sh && ls -lh /root/scripts_csi_dog/'
```

### 3.3 Verify the OpenWrt radio prerequisites

On STA12:

```bash
iw dev wlan0 link
ip addr show dev wlan0
test -f /etc/wpa_supplicant.conf && echo WPA_CONFIG_OK
```

For the finite dataset capture, `capture_csi.sh` starts:

```bash
wpa_supplicant -D nl80211 -i wlan0 -c /etc/wpa_supplicant.conf -B
```

so `/etc/wpa_supplicant.conf` must be valid on that device.

For the live demo, the host-side launcher creates `/tmp/ap_mac.bin` on STA12 before starting the streamer. The live streamer checks that this file contains exactly six raw MAC bytes before triggering CSI.

### 3.4 Verify the reverse SSH path used by live CSI

The live streamer sends CSI measurements from STA12 back to the PC/Spark. It expects the MikroTik-side key:

```text
/root/.ssh/id_rsa_dropbear
```

and the corresponding public key must be authorized for the PC/Spark Linux user used by the demo.

From STA12, verify:

```bash
ssh -i /root/.ssh/id_rsa_dropbear nextnet@192.168.1.170 'echo PC_OK'
```

Adjust user/IP if your deployment differs.

## 3.5 What the current no-Raspi launcher does automatically

`go2_dt/run_full_demo_current.sh` starts the PC-side orchestration. Its radio sub-launcher now copies the tracked:

```text
deployment/openwrt/scripts_csi_dog/stream_csi_live_05s_single.sh
```

to:

```text
/root/scripts_csi_dog/stream_csi_live_05s_single.sh
```

on STA12 before CSI starts. Therefore the live streamer is kept in sync with the repository on every full-demo launch.

This automatic deployment covers the **live streamer only**. The dataset helpers `capture_csi.sh` and `capture_countdown.sh` should still be provisioned with the first-time installation procedure above when dataset collection is needed.

## 3.6 Manual tests

Live streamer, executed on STA12:

```bash
/root/scripts_csi_dog/stream_csi_live_05s_single.sh 192.168.1.170 nextnet
```

Finite dataset capture, executed on STA12:

```bash
/root/scripts_csi_dog/capture_csi.sh person 120 1
```

Repeated dataset capture:

```bash
/root/scripts_csi_dog/capture_countdown.sh person 120 10 1 5
```

Verify the live file on the PC/Spark:

```bash
tail -f /home/nextnet/AlbertoDir/go2_dt/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt
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
