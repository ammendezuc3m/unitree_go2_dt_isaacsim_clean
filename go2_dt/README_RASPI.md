# Raspi + AP13/STA12 demo

This document is the operational reference for the Raspberry Pi architecture. For a machine/device being prepared for the first time, start with `../docs/setup_from_scratch.md`.

## 1. Architecture

```text
Spark 172.16.12.170
    |
    | Ethernet
    |
AP13
  mgmt: 192.168.1.13
  wlan0: 10.10.10.1
    |
    | 60 GHz
    |
STA12
  mgmt: 192.168.1.12
  wlan0: 10.10.10.2
    |
    | Ethernet
    |
Raspi 172.16.13.100
```

Spark runs ROS 2, Isaac Sim, YOLO, CSI inference and receivers. The Raspberry Pi generates camera/MP4/iperf traffic. CSI is triggered on STA12 itself.

## 2. Required files already installed on external devices

Before setup/run, the following must exist.

### STA12 MikroTik/OpenWrt

```text
/root/scripts_csi_dog/
├── capture_csi.sh
├── capture_countdown.sh
├── stream_csi_live_05s_single.sh
└── stream_csi_stdout_05s_single.sh
```

The full Raspi demo uses `stream_csi_stdout_05s_single.sh`: Spark connects through the Raspi to STA12 and executes that file remotely. The vendor command therefore runs **on the MikroTik**.

### Raspberry Pi

```text
/home/system/raspi_60ghz_demo/
├── raspi_60ghz_sender.sh
├── iperf_client_loop.sh
└── mp4_loop_tx_6002.sh
```

Media:

```text
/home/nextnet/raspi_60ghz_demo/golden_test.mp4
```

The setup script configures the network. It does **not** install these files. See `../deployment/README.md` for provisioning commands.

## 3. Host prerequisites

The repository must already have:

- Git LFS assets pulled;
- `go2_dt/.venv` installed from `requirements/requirements-runtime.txt`;
- the ROS 2 workspace built;
- Docker/NVIDIA/Isaac Sim available.

See `../requirements/README.md`.

## 4. Normal startup sequence

### 4.1 Stop leftovers

```bash
cd /home/nextnet/AlbertoDir/go2_dt

RASPI_MGMT_HOST=<raspi-management-ip> \
RASPI_USER=nextnet \
./stop_full_demo_ap13_sta12_raspi_clean.sh
```

### 4.2 Configure AP13/STA12/Raspi routes

```bash
RASPI_MGMT_HOST=<raspi-management-ip> \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh
```

This setup configures:

- Spark demo IP `172.16.12.170`;
- Raspi demo IP `172.16.13.100`;
- AP13/STA12 radio addresses `10.10.10.1/24` and `10.10.10.2/24`;
- `hostapd` on AP13;
- `wpa_supplicant` on STA12;
- forwarding and routes across both subnets.

### 4.3 Verify

```bash
ping -c 3 172.16.13.100

ssh nextnet@172.16.13.100 '
  test -x /home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh &&
  test -x /home/system/raspi_60ghz_demo/mp4_loop_tx_6002.sh &&
  test -f /home/nextnet/raspi_60ghz_demo/golden_test.mp4 &&
  echo RASPI_RUNTIME_OK
'
```

STA12 check through Raspi management:

```bash
ssh nextnet@<raspi-management-ip> \
  "ssh -oHostKeyAlgorithms=+ssh-rsa root@192.168.1.12 '
    iw dev wlan0 link
    test -x /root/scripts_csi_dog/stream_csi_stdout_05s_single.sh &&
    echo CSI_RUNTIME_OK
  '"
```

### 4.4 Run

```bash
./run_full_demo_ap13_sta12_raspi.sh
```

The run script intentionally assumes the network setup is already healthy.

## 5. Data flows

| Flow | Producer | Consumer | Destination |
|---|---|---|---|
| Camera | Raspi `raspi_60ghz_sender.sh` | Spark YOLO receiver | UDP 6000 |
| Golden MP4 | Raspi `mp4_loop_tx_6002.sh` | Spark GStreamer RX | UDP 6002 |
| iperf | Raspi `iperf_client_loop.sh` | Spark iperf server | 5201 |
| CSI | STA12 `stream_csi_stdout_05s_single.sh` | Spark CSI predictor | SSH stdout -> local file |

CSI flow in more detail:

```text
Spark
  -> SSH Raspi management
      -> SSH STA12
          -> /root/scripts_csi_dog/stream_csi_stdout_05s_single.sh
              -> iw dev wlan0 vendor recv ...
              -> dmesg [AOA] Measurement
          -> stdout
      -> stdout
  -> realtime_inputs/live_csi_stream.txt
  -> csi_live_predictor_from_stream.py
  -> analysis_outputs/live_prediction_state.json
```

## 6. Useful isolated checks

Camera packets:

```bash
timeout 5 sudo tcpdump -ni enP7s7 'udp port 6000' -c 20
```

MP4 packets:

```bash
timeout 5 sudo tcpdump -ni enP7s7 'udp port 6002' -c 20
```

iperf:

```bash
timeout 5 sudo tcpdump -ni enP7s7 'port 5201' -c 20
```

CSI only:

```bash
cd /home/nextnet/AlbertoDir/go2_dt
RASPI_MGMT_HOST=<raspi-management-ip> \
./tests_experiments/raspi_60ghz_ap13_sta12/csi_sta12_stream_to_file.sh
```

Raspi logs:

```bash
ssh nextnet@172.16.13.100 'tail -n 100 /home/system/raspi_60ghz_demo/logs/*.log'
```

## 7. Stop

```bash
RASPI_MGMT_HOST=<raspi-management-ip> \
RASPI_USER=nextnet \
./stop_full_demo_ap13_sta12_raspi_clean.sh
```

## 8. Known path technical debt

The validated stack still contains historical absolute references to:

```text
/home/nextnet/AlbertoDir/go2_dt
```

This is intentionally documented rather than silently refactored in this cleanup. Removing those assumptions affects ROS launch files, Isaac mounts and multiple runtime JSON paths and should be validated on the physical testbed.
