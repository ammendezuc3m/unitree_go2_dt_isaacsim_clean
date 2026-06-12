#!/usr/bin/env bash
set -Eeuo pipefail

RASPI_HOST="${RASPI_HOST:-172.16.12.100}"
RASPI_MGMT_HOST="${RASPI_MGMT_HOST:-192.168.1.100}"

stop_spark() {
  echo "=== STOP Spark side ==="

  pkill -9 -f "rx_camera_rtp_yolox_pipe.py" 2>/dev/null || true
  pkill -9 -f "rx_camera_rtp_yolox.py" 2>/dev/null || true
  pkill -9 -f "throughput_live_tk.py" 2>/dev/null || true
  pkill -9 -f "csi_live_predictor_from_stream.py" 2>/dev/null || true
  pkill -9 -f "gst-launch-1.0" 2>/dev/null || true
  pkill -9 -f "iperf3" 2>/dev/null || true

  sudo fuser -k 5201/tcp 2>/dev/null || true
  sudo fuser -k 5201/udp 2>/dev/null || true
  sudo fuser -k 6000/udp 2>/dev/null || true
  sudo fuser -k 6002/udp 2>/dev/null || true
}

stop_raspi_host() {
  local host="$1"

  echo
  echo "=== STOP Raspi side via ${host} ==="

  ssh -o ConnectTimeout=5 "system@${host}" '
echo "--- before ---"
ps -ef | grep -E "iperf3|gst-launch|v4l2src|x264enc|raspi_60ghz_sender|while true|mp4-loop|iperf-loop" | grep -v grep || true

echo
echo "--- killing by PID ---"
for pid in $(ps -eo pid,args | awk "/iperf3|gst-launch-1.0|v4l2src|x264enc|raspi_60ghz_sender|while true|mp4-loop|iperf-loop/ && !/awk/ {print \$1}"); do
  echo "kill -9 $pid"
  kill -9 "$pid" 2>/dev/null || true
done

killall -9 iperf3 2>/dev/null || true
killall -9 gst-launch-1.0 2>/dev/null || true

sleep 1

echo
echo "--- after ---"
ps -ef | grep -E "iperf3|gst-launch|v4l2src|x264enc|raspi_60ghz_sender|while true|mp4-loop|iperf-loop" | grep -v grep || echo "Raspi limpia"
'
}

stop_spark

if ! stop_raspi_host "${RASPI_HOST}"; then
  echo
  echo "[stop] No pude parar por ${RASPI_HOST}; pruebo gestión ${RASPI_MGMT_HOST}"
  stop_raspi_host "${RASPI_MGMT_HOST}" || true
fi

echo
echo "=== Remaining Spark ==="
ps -ef | grep -E "iperf3|gst-launch|rx_camera_rtp_yolox|throughput_live_tk" | grep -v grep || echo "Spark limpia"

echo
echo "=== STOP complete ==="
