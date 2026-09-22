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

Live streamer manual test from the PC/Spark repository root:

```bash
REPO_ROOT="$(pwd)"
CSI_STREAM_FILE="${REPO_ROOT}/go2_dt/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt"

ssh root@192.168.1.12 \
  "/root/scripts_csi_dog/stream_csi_live_05s_single.sh 192.168.1.170 '${USER}' '${CSI_STREAM_FILE}'"
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
tail -f go2_dt/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt
```

---

## 4. Raspberry Pi runtime

Repository source of truth:

```text
deployment/raspi/raspi_60ghz_demo/
├── raspi_60ghz_sender.sh
├── iperf_client_loop.sh
└── mp4_loop_tx_6002.sh
```

The validated demo media is stored on the PC/Spark as:

```text
go2_dt/media/golden_test.mp4
```

During the AP13/STA12/Raspi setup, the PC/Spark provisions the Raspberry Pi automatically:

```text
~/raspi_60ghz_demo/
├── raspi_60ghz_sender.sh
├── iperf_client_loop.sh
└── mp4_loop_tx_6002.sh

/home/<RASPI_USER>/raspi_60ghz_demo/
└── golden_test.mp4
```

The setup checks that `gst-launch-1.0` and `iperf3` exist on the Raspberry Pi and fails early if the MP4 is only an unpulled Git LFS pointer.

### 4.1 What each Raspberry Pi script does

- `raspi_60ghz_sender.sh`: main sender. It can transmit the USB camera, the MP4 stream and iperf traffic toward the Spark.
- `mp4_loop_tx_6002.sh`: persistent MP4 loop sender. This is the loop mechanism used for the repeated `golden_test.mp4` playback over RTP/UDP port 6002.
- `iperf_client_loop.sh`: persistent iperf generator.

When `ENABLE_MP4=1`, the current sender uses `mp4_loop_tx_6002.sh` by default so the video restarts automatically when it reaches the end.

### 4.2 Important architecture detail

The Raspberry Pi video scripts do **not** configure the MikroTik radio themselves. Radio configuration is done by the host-side setup script.

In the validated topology:

```text
Spark/PC -- Ethernet --> AP13 -- 60 GHz --> STA12 -- Ethernet --> Raspberry Pi
```

the PC can reach AP13 directly. STA12 is on the Raspberry Pi side, so the setup uses the Raspberry Pi as an SSH jump host:

```text
PC/Spark
  -> SSH Raspberry Pi
     -> SSH root@STA12
```

That is why the setup can create the STA `wpa_supplicant` configuration and install CSI scripts even when STA12 is not directly reachable from the PC management side.

## 5. Canonical first-time setup

Before running the external-device setup on a fresh clone, prepare the host repository first:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "${REPO_ROOT}"

git lfs install
git lfs pull
bash requirements/check_lfs_assets.sh
bash requirements/setup_host_env.sh
```

The setup needs the real `golden_test.mp4`, not a Git LFS pointer. The same LFS validation also confirms the YOLO weights, CSI model and validated Isaac scene used later by the full demo.

You do **not** need to manually pre-copy the Raspberry Pi sender scripts or the STA12 CSI scripts if you use the canonical setup below: it provisions them from the repository every time.


The canonical setup command is:

```bash
cd go2_dt

RASPI_MGMT_HOST=<raspi-management-ip> \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh
```

The setup now performs these stages in order:

1. Detect/reach the Raspberry Pi over its management connection.
2. Provision the Raspberry Pi scripts from `deployment/raspi/raspi_60ghz_demo/`.
3. Copy `go2_dt/media/golden_test.mp4` to the Raspberry Pi.
4. Provision the tracked CSI scripts to STA12 under `/root/scripts_csi_dog/`, using the Raspberry Pi as the SSH hop.
5. Configure Spark-side addresses/routes.
6. Configure Raspberry Pi Ethernet addresses/routes.
7. Reset/configure AP13 and create its `hostapd` configuration.
8. Reset/configure STA12 and create its `wpa_supplicant` configuration.
9. Bring up the AP13↔STA12 60 GHz link.
10. Verify radio connectivity and end-to-end Spark↔Raspberry Pi connectivity.

AP13 does not need the CSI capture scripts: its required `hostapd` configuration is generated by the setup itself. STA12 receives the CSI scripts because CSI triggering/capture executes there.

After setup, the important checks are:

```bash
ssh "${RASPI_USER:-nextnet}@<raspi-management-ip>" \
  'ls -lh ~/raspi_60ghz_demo/ && ls -lh ~/raspi_60ghz_demo/golden_test.mp4'

ssh nextnet@<raspi-management-ip> \
  "ssh root@192.168.1.12 'ls -lh /root/scripts_csi_dog/'"
```

Then the Raspi pipeline can be launched from the PC/Spark with:

```bash
RASPI_HOST=172.16.13.100 \
RASPI_USER=nextnet \
SPARK_IP=172.16.12.170 \
ENABLE_CAMERA=1 \
ENABLE_MP4=1 \
ENABLE_IPERF=1 \
./tests_experiments/raspi_60ghz_ap13_sta12/run_ap13_sta12_pipeline.sh
```

The host-side pipeline starts the Raspberry Pi sender remotely; it does not require manually recreating the sender scripts on the Raspberry Pi first.

