# Setup from scratch

This document describes the complete deployment order for the Go2 digital-twin demo. It distinguishes clearly between code that runs on the **Spark/main PC**, code that must be installed on the **MikroTik/OpenWrt devices**, and code that must be installed on the **Raspberry Pi**.

The important point is that the full demo does **not** magically transfer all runtime scripts every time it starts. Device deployment is a separate provisioning step. Once the device scripts are installed, the Spark launchers connect to the remote devices over SSH and start those scripts there.

## 1. Roles

### Spark / main PC

Runs:

- ROS 2 Jazzy and the Unitree Go2 SDK;
- SLAM/LiDAR processing;
- the patrol controller;
- CSI inference;
- YOLO inference;
- Isaac Sim;
- MP4/camera receivers;
- iperf server and throughput plots;
- SSH orchestration for the Raspberry Pi and MikroTik devices.

Repository checkout lives on this machine.

### MikroTik/OpenWrt AP/STA

The 60 GHz radio configuration and CSI trigger happen **on the MikroTik/OpenWrt device itself**.

For CSI, the remote shell script ultimately executes the vendor command on the STA:

```sh
cat "$MAC_BIN" | iw dev wlan0 vendor recv 0x001374 0x93 -
```

The six bytes in `MAC_BIN` are the MAC address of the currently associated peer/AP. The current scripts derive that BSSID from `iw dev wlan0 link` instead of tying the repository to one historical MAC address.

The dataset capture script also controls the local STA association with:

```sh
wpa_supplicant -D nl80211 -i wlan0 -c /etc/wpa_supplicant.conf -B
```

Therefore these are **device-side scripts**. The PC can invoke them over SSH, but the `wpa_supplicant`, `iw ... vendor recv` and `dmesg` commands execute on OpenWrt.

### Raspberry Pi

Runs the traffic generators:

- USB camera -> H264/RTP -> Spark UDP 6000;
- MP4 loop -> H264/RTP -> Spark UDP 6002;
- iperf client -> Spark port 5201.

The Pi does not run YOLO in the validated architecture; YOLO inference is performed on Spark after receiving the camera stream.

## 2. Fresh host checkout

Recommended host: Ubuntu 24.04 with ROS 2 Jazzy.

```bash
git clone <repository-url>
cd unitree_go2_dt_isaacsim_clean

git lfs install
git lfs pull
```

Then follow `requirements/README.md` to install system packages, create `go2_dt/.venv`, install `requirements-runtime.txt`, run rosdep and build the workspace.

Do not skip Git LFS. The YOLO checkpoint, CSI model, MP4 and Isaac assets are LFS objects.

## 3. First-time MikroTik/OpenWrt provisioning

Repository files intended for OpenWrt are under:

```text
deployment/openwrt/scripts_csi_dog/
```

Current deployed set:

```text
capture_csi.sh
capture_countdown.sh
stream_csi_live_05s_single.sh
stream_csi_stdout_05s_single.sh
```

Install them under:

```text
/root/scripts_csi_dog/
```

### 3.1 Direct Ethernet commissioning

When provisioning a MikroTik for the first time, connect the Spark/laptop by Ethernet to the management side of the radio and put a management address on the PC interface. Example for the validated management subnet:

```bash
sudo ip addr add 192.168.1.170/24 dev enP7s7
```

For STA12:

```bash
ping -c 3 192.168.1.12
ssh root@192.168.1.12 'mkdir -p /root/scripts_csi_dog'

scp -O deployment/openwrt/scripts_csi_dog/*.sh \
  root@192.168.1.12:/root/scripts_csi_dog/

ssh root@192.168.1.12 'chmod +x /root/scripts_csi_dog/*.sh'
```

For AP13, replace `192.168.1.12` with `192.168.1.13` if a script is intentionally required on the AP.

Verify:

```bash
ssh root@192.168.1.12 'ls -lh /root/scripts_csi_dog'
```

### 3.2 What each OpenWrt script does

`capture_csi.sh`

- runs on the MikroTik STA;
- restarts the local `wpa_supplicant`;
- waits for the 60 GHz association;
- derives the associated peer BSSID;
- sends the BSSID as six raw bytes to the Wil6210 vendor CSI command;
- reads `[AOA] Measurement:` lines from the local kernel log;
- stores a finite labelled capture under `/tmp/csi_dog_dataset`.

`capture_countdown.sh`

- runs on the MikroTik;
- repeatedly calls `capture_csi.sh`;
- provides countdown/rest periods for labelled dataset collection.

`stream_csi_stdout_05s_single.sh`

- runs on the MikroTik STA;
- assumes the radio association has already been configured by the setup phase;
- triggers CSI about every 0.5 s;
- prints valid measurements to stdout;
- is the preferred script for the Raspi/full demo because Spark can execute it over nested SSH and save stdout locally.

`stream_csi_live_05s_single.sh`

- alternative push mode;
- runs on OpenWrt and opens an SSH append pipe back to Spark;
- useful when OpenWrt itself should write directly into the Spark CSI input file.

## 4. First-time Raspberry Pi provisioning

Files intended for the Pi are under:

```text
deployment/raspi/raspi_60ghz_demo/
```

Current runtime set:

```text
raspi_60ghz_sender.sh
iperf_client_loop.sh
mp4_loop_tx_6002.sh
```

Install them under:

```text
/home/system/raspi_60ghz_demo/
```

Example, using the Pi management address:

```bash
RASPI_MGMT_HOST=192.168.1.100
RASPI_USER=nextnet

ssh "$RASPI_USER@$RASPI_MGMT_HOST" \
  'sudo mkdir -p /home/system/raspi_60ghz_demo && sudo chown -R nextnet:nextnet /home/system/raspi_60ghz_demo'

scp deployment/raspi/raspi_60ghz_demo/*.sh \
  "$RASPI_USER@$RASPI_MGMT_HOST:/home/system/raspi_60ghz_demo/"

ssh "$RASPI_USER@$RASPI_MGMT_HOST" \
  'chmod +x /home/system/raspi_60ghz_demo/*.sh'
```

The MP4 media file is kept separately under the Pi user's home:

```text
/home/nextnet/raspi_60ghz_demo/golden_test.mp4
```

Copy it from the LFS-restored repository:

```bash
ssh "$RASPI_USER@$RASPI_MGMT_HOST" 'mkdir -p /home/nextnet/raspi_60ghz_demo'
scp go2_dt/media/golden_test.mp4 \
  "$RASPI_USER@$RASPI_MGMT_HOST:/home/nextnet/raspi_60ghz_demo/golden_test.mp4"
```

Verify:

```bash
ssh "$RASPI_USER@$RASPI_MGMT_HOST" '
  test -x /home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh &&
  test -x /home/system/raspi_60ghz_demo/iperf_client_loop.sh &&
  test -x /home/system/raspi_60ghz_demo/mp4_loop_tx_6002.sh &&
  test -f /home/nextnet/raspi_60ghz_demo/golden_test.mp4 &&
  echo RASPI_DEPLOYMENT_OK
'
```

## 5. Setup phase: configure topology and routes

Provisioning and setup are separate operations.

The setup script **does not copy the repository to the devices**. It assumes the remote runtime files above have already been installed.

For the validated AP13/STA12/Raspi topology:

```text
Spark enP7s7: 172.16.12.170/24
AP13 br-lan:   172.16.12.1/24
AP13 wlan0:    10.10.10.1/24
STA12 wlan0:   10.10.10.2/24
STA12 br-lan:  172.16.13.1/24
Raspi eth0:    172.16.13.100/24
```

Management:

```text
AP13:  192.168.1.13
STA12: 192.168.1.12, reached through the Raspi in the deployed topology
Raspi: RASPI_MGMT_HOST, often WiFi/campus management
```

Run:

```bash
cd go2_dt

RASPI_MGMT_HOST=<raspi-management-ip> \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh
```

This phase:

1. puts the Spark and Raspi demo addresses on the Ethernet interfaces;
2. writes the temporary AP/STA 60 GHz configuration;
3. starts `hostapd` on AP13;
4. starts `wpa_supplicant` on STA12;
5. assigns the 10.10.10.1/10.10.10.2 radio addresses;
6. enables forwarding and installs the routes between 172.16.12.0/24 and 172.16.13.0/24;
7. verifies AP <-> STA radio connectivity and Spark <-> Raspi end-to-end connectivity.

The full runtime launcher deliberately does not repeat this network setup.

## 6. Validate before launching the full demo

Check the radio:

```bash
ssh root@192.168.1.13 'ping -I wlan0 -c 3 10.10.10.2'
```

From the Raspi management path, check STA:

```bash
ssh nextnet@<raspi-management-ip> \
  "ssh -oHostKeyAlgorithms=+ssh-rsa root@192.168.1.12 'iw dev wlan0 link; ping -I wlan0 -c 3 10.10.10.1'"
```

Check end-to-end:

```bash
ping -c 3 172.16.13.100
ssh nextnet@172.16.13.100 'ping -c 3 172.16.12.170'
```

Check remote runtime files:

```bash
ssh nextnet@172.16.13.100 'ls -l /home/system/raspi_60ghz_demo/'
ssh nextnet@<raspi-management-ip> \
  "ssh root@192.168.1.12 'ls -l /root/scripts_csi_dog/'"
```

## 7. Run the Raspi/full-radio demo

Only after provisioning + setup + validation:

```bash
cd go2_dt
./run_full_demo_ap13_sta12_raspi.sh
```

The Spark launcher then:

- starts Isaac Sim;
- starts Go2 ROS 2 + SLAM;
- starts Spark-side camera/MP4 receivers and YOLO;
- starts iperf server and throughput visualization;
- SSHes into the Pi to start camera/iperf/MP4 traffic;
- SSHes through the Pi into STA12 to execute `/root/scripts_csi_dog/stream_csi_stdout_05s_single.sh`;
- feeds CSI stdout into the Spark predictor;
- starts the patrol controller.

That remote execution distinction is intentional: **Spark orchestrates, but the radio-specific CSI command executes on the MikroTik**.

## 8. No-Raspi mode

The no-Raspi mode has a different physical arrangement: cameras are local to Spark and Spark can access AP/STA management directly.

Run:

```bash
cd go2_dt
./run_full_demo_current.sh
```

The no-Raspi launcher currently contains its own validated radio setup/orchestration logic. Do not mix the two topologies without checking the IP plan.

## 9. Stop

No-Raspi:

```bash
./stop_full_demo_current.sh
```

Raspi:

```bash
RASPI_MGMT_HOST=<raspi-management-ip> \
RASPI_USER=nextnet \
./stop_full_demo_ap13_sta12_raspi_clean.sh
```

## 10. Known technical debt

Several active scripts still assume the historical project path:

```text
/home/nextnet/AlbertoDir/go2_dt
```

and Isaac expects the host project under `/workspace`.

This is known technical debt. The current documented workaround is to keep the validated path or use the symlinks described in the root README. A future cleanup should derive paths from the repository location and remove those assumptions, but that change should be tested against the physical demo rather than performed as a blind refactor.
