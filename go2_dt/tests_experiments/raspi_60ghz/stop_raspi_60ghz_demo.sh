#!/usr/bin/env bash
set -Eeuo pipefail

RASPI_USER="${RASPI_USER:-system}"
RASPI_HOST="${RASPI_HOST:-172.16.12.100}"
STA_HOST="${STA_HOST:-192.168.1.13}"

echo "=== STOP Spark side ==="

pkill -f "rx_camera_rtp_yolox.py" 2>/dev/null || true
pkill -f "throughput_live_tk.py" 2>/dev/null || true
pkill -f "csi_live_predictor_from_stream.py" 2>/dev/null || true
pkill -f "CSI pull STA13" 2>/dev/null || true
pkill -f "gst-launch-1.0.*udpsrc.*6000" 2>/dev/null || true
pkill -f "gst-launch-1.0.*udpsrc.*6002" 2>/dev/null || true
pkill -f "iperf3 -s" 2>/dev/null || true
pkill -f "iperf3.*5201" 2>/dev/null || true

sudo fuser -k 6000/udp 2>/dev/null || true
sudo fuser -k 6002/udp 2>/dev/null || true
sudo fuser -k 5201/tcp 2>/dev/null || true
sudo fuser -k 5201/udp 2>/dev/null || true

echo
echo "=== STOP CSI on STA .13 ==="
ssh -oHostKeyAlgorithms=+ssh-rsa \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    -oStrictHostKeyChecking=accept-new \
    root@"${STA_HOST}" '
pkill -9 -f "iw dev wlan0 vendor" 2>/dev/null || true
pkill -9 -f "vendor recv" 2>/dev/null || true
killall -9 iw 2>/dev/null || true
' || true

echo
echo "=== STOP Raspi side ==="
ssh -o StrictHostKeyChecking=accept-new "${RASPI_USER}@${RASPI_HOST}" '
/home/system/raspi_60ghz_demo/stop_raspi_60ghz_sender.sh 2>/dev/null || true
pkill -f "gst-launch-1.0.*udpsink" 2>/dev/null || true
pkill -f "iperf3" 2>/dev/null || true
killall -9 iperf3 2>/dev/null || true
' || true

echo
echo "=== Remaining local ==="
ps -eo pid,ppid,pcpu,pmem,cmd | grep -E "rx_camera_rtp_yolox|throughput_live_tk|csi_live|gst-launch|iperf3|CSI pull" | grep -v grep || true

echo
echo "=== STOP complete ==="
