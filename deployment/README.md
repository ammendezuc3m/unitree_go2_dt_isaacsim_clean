# Device deployment

This directory contains the scripts that are **copied out of the Git repository and installed on the external devices**.

They do not normally run on Spark directly.

```text
deployment/
├── openwrt/
│   └── scripts_csi_dog/
│       ├── capture_csi.sh
│       ├── capture_countdown.sh
│       ├── stream_csi_live_05s_single.sh
│       └── stream_csi_stdout_05s_single.sh
└── raspi/
    └── raspi_60ghz_demo/
        ├── raspi_60ghz_sender.sh
        ├── iperf_client_loop.sh
        └── mp4_loop_tx_6002.sh
```

For the complete commissioning order, see `docs/setup_from_scratch.md`.

## 1. MikroTik/OpenWrt

Install the scripts on the relevant OpenWrt STA under:

```text
/root/scripts_csi_dog/
```

Example from Spark while directly connected to the management network:

```bash
sudo ip addr add 192.168.1.170/24 dev enP7s7

ssh root@192.168.1.12 'mkdir -p /root/scripts_csi_dog'
scp -O openwrt/scripts_csi_dog/*.sh root@192.168.1.12:/root/scripts_csi_dog/
ssh root@192.168.1.12 'chmod +x /root/scripts_csi_dog/*.sh'
```

The important architectural point is:

> Spark may start a CSI script over SSH, but the CSI trigger itself runs on the MikroTik/OpenWrt radio.

For example, the device-side script ultimately executes:

```sh
cat "$MAC_BIN" | iw dev wlan0 vendor recv 0x001374 0x93 -
```

The six raw bytes in `MAC_BIN` are derived from the currently associated BSSID.

### `capture_csi.sh`

Finite labelled dataset capture. It runs locally on OpenWrt, restarts `wpa_supplicant`, waits for association, triggers CSI and writes measurements below `/tmp/csi_dog_dataset`.

### `capture_countdown.sh`

Batch/countdown wrapper around `capture_csi.sh`.

### `stream_csi_stdout_05s_single.sh`

Preferred live streamer for the Raspi/full demo. It runs on STA12 and prints measurements to stdout. Spark reaches it through nested SSH and saves the output to:

```text
go2_dt/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt
```

### `stream_csi_live_05s_single.sh`

Alternative push mode. OpenWrt itself opens an SSH append pipe to Spark. Usage:

```bash
/root/scripts_csi_dog/stream_csi_live_05s_single.sh <PC_IP> <PC_USER> [PC_FILE]
```

## 2. Raspberry Pi

Install runtime scripts under:

```text
/home/system/raspi_60ghz_demo/
```

Example:

```bash
RASPI_MGMT_HOST=192.168.1.100
RASPI_USER=nextnet

ssh "$RASPI_USER@$RASPI_MGMT_HOST" \
  'sudo mkdir -p /home/system/raspi_60ghz_demo && sudo chown -R nextnet:nextnet /home/system/raspi_60ghz_demo'

scp raspi/raspi_60ghz_demo/*.sh \
  "$RASPI_USER@$RASPI_MGMT_HOST:/home/system/raspi_60ghz_demo/"

ssh "$RASPI_USER@$RASPI_MGMT_HOST" \
  'chmod +x /home/system/raspi_60ghz_demo/*.sh'
```

The current scripts are:

- `raspi_60ghz_sender.sh`: camera RTP/H264 and iperf; can also send MP4 when enabled.
- `iperf_client_loop.sh`: restarts iperf if a long run ends or the connection drops.
- `mp4_loop_tx_6002.sh`: persistent golden-MP4 sender used by the full demo.

The validated MP4 is stored separately on the Pi:

```text
/home/nextnet/raspi_60ghz_demo/golden_test.mp4
```

Copy it from the repository after Git LFS has restored it:

```bash
ssh "$RASPI_USER@$RASPI_MGMT_HOST" 'mkdir -p /home/nextnet/raspi_60ghz_demo'
scp ../go2_dt/media/golden_test.mp4 \
  "$RASPI_USER@$RASPI_MGMT_HOST:/home/nextnet/raspi_60ghz_demo/golden_test.mp4"
```

## 3. Provisioning is not the network setup phase

These are separate:

1. **Provision devices**: copy the scripts above once, or whenever they change.
2. **Run network setup**: configure AP13/STA12 association, demo IPs, forwarding and routes.
3. **Run the demo**: Spark invokes the already-installed remote scripts.

The network setup command is:

```bash
cd ../go2_dt

RASPI_MGMT_HOST=<raspi-management-ip> \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh
```

That setup script configures connectivity. It is not intended to be the software installer for the Pi or MikroTik.

## 4. Verify deployment before a demo

```bash
ssh nextnet@<raspi-management-ip> '
  test -x /home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh &&
  test -x /home/system/raspi_60ghz_demo/iperf_client_loop.sh &&
  test -x /home/system/raspi_60ghz_demo/mp4_loop_tx_6002.sh &&
  echo RASPI_OK
'
```

For STA12 through the Raspi:

```bash
ssh nextnet@<raspi-management-ip> \
  "ssh root@192.168.1.12 '
    test -x /root/scripts_csi_dog/capture_csi.sh &&
    test -x /root/scripts_csi_dog/stream_csi_stdout_05s_single.sh &&
    echo STA_CSI_OK
  '"
```
