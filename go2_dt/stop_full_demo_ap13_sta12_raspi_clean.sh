#!/usr/bin/env bash

# Portable: host paths are derived from this script location.
set -u

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GO2_ROOT="${SCRIPT_DIR}"

RASPI_HOST="${RASPI_HOST:-172.16.13.100}"
RASPI_USER="${RASPI_USER:-nextnet}"

detect_raspi_mgmt_host() {
  local candidates=(
    "${RASPI_MGMT_HOST:-}"
    "192.168.1.100"
    "10.39.251.226"
    "172.16.13.100"
    "nextnet.local"
    "raspberrypi.local"
  )

  local h
  for h in "${candidates[@]}"; do
    [ -z "$h" ] && continue
    if ssh \
      -o BatchMode=yes \
      -o ConnectTimeout=2 \
      -o StrictHostKeyChecking=accept-new \
      "${RASPI_USER}@${h}" 'echo RASPI_OK' 2>/dev/null | grep -q "RASPI_OK"; then
      echo "$h"
      return 0
    fi
  done

  return 1
}

if [ -z "${RASPI_MGMT_HOST:-}" ]; then
  RASPI_MGMT_HOST="$(detect_raspi_mgmt_host || true)"
fi

if [ -z "${RASPI_MGMT_HOST:-}" ]; then
  echo "[ERROR] No he podido detectar la Raspi por SSH."
  echo "[ERROR] IPs probadas: 192.168.1.100, 10.39.251.226, 172.16.13.100, nextnet.local, raspberrypi.local"
  echo "[ERROR] Comprueba manualmente:"
  echo "  ping -c 3 192.168.1.100"
  echo "  ping -c 3 10.39.251.226"
  echo "  ssh nextnet@192.168.1.100"
  exit 1
fi

export RASPI_MGMT_HOST RASPI_USER
echo "[AUTO] Raspi detectada: ${RASPI_USER}@${RASPI_MGMT_HOST}"
RASPI_USER="${RASPI_USER:-nextnet}"
STA_HOST="${STA_HOST:-192.168.1.12}"

log() {
  echo -e "\033[1;32m[stop-full-demo]\033[0m $*"
}

warn() {
  echo -e "\033[1;33m[stop-full-demo][WARN]\033[0m $*" >&2
}

log "Parando procesos locales lanzados por run..."

# Wrappers principales.
pkill -TERM -f "run_full_demo_ap13_sta12_raspi.sh" 2>/dev/null || true
pkill -TERM -f "run_demo_ap13_sta12_no_setup.sh" 2>/dev/null || true
pkill -TERM -f "launch_r6_mp4_clean.sh" 2>/dev/null || true
pkill -TERM -f "launch_raspi_mp4_loop_window.sh" 2>/dev/null || true

# MP4 RX local Spark.
pkill -TERM -f "mp4_rx_loop_6002.sh" 2>/dev/null || true
pkill -TERM -f "gst-launch-1.0.*udpsrc.*6002" 2>/dev/null || true
pkill -TERM -f "gst-launch-1.0.*6002" 2>/dev/null || true

# YOLO / cámara RX.
pkill -TERM -f "rx_camera_rtp_yolox_realtime_pipe.py" 2>/dev/null || true
pkill -TERM -f "rx_camera_rtp_yolox_realtime.py" 2>/dev/null || true
pkill -TERM -f "rx_camera_rtp_yolox_pipe.py" 2>/dev/null || true
pkill -TERM -f "rx_camera_rtp_yolox.py" 2>/dev/null || true

# CSI local.
pkill -TERM -f "csi_live_predictor_from_stream.py" 2>/dev/null || true
pkill -TERM -f "ssh.*stream_csi_stdout_05s_ap13.sh" 2>/dev/null || true

# Zone loop / ROS visual.
pkill -TERM -f "zone_loop_patrol_v3.py" 2>/dev/null || true
pkill -TERM -f "zone_loop_patrol" 2>/dev/null || true
pkill -TERM -f "slam_toolbox" 2>/dev/null || true
pkill -TERM -f "go2_slam" 2>/dev/null || true
pkill -TERM -f "rviz2" 2>/dev/null || true
pkill -TERM -f "ros2 launch.*slam" 2>/dev/null || true
pkill -TERM -f "go2_rect_motion" 2>/dev/null || true

# Throughput / iperf local.
pkill -TERM -f "throughput_local_rx_tk_smooth.py" 2>/dev/null || true
pkill -TERM -f "throughput_local_rx_tk.py" 2>/dev/null || true
pkill -TERM -f "throughput_live_tk.py" 2>/dev/null || true
pkill -TERM -f "throughput_live_plot.py" 2>/dev/null || true
pkill -TERM -f "iperf3 -s" 2>/dev/null || true

# Isaac Sim / Docker.
log "Parando Isaac Sim / Docker..."
docker rm -f isaac-sim-gui 2>/dev/null || true
docker rm -f isaac-sim 2>/dev/null || true
docker ps --format '{{.ID}} {{.Names}} {{.Image}}' 2>/dev/null | grep -Ei 'isaac|omniverse|nvcr.io/nvidia/isaac' | awk '{print $1}' | xargs -r docker rm -f 2>/dev/null || true

sleep 2

log "Kill fuerte si quedan restos locales..."

pkill -KILL -f "run_full_demo_ap13_sta12_raspi.sh" 2>/dev/null || true
pkill -KILL -f "run_demo_ap13_sta12_no_setup.sh" 2>/dev/null || true
pkill -KILL -f "launch_r6_mp4_clean.sh" 2>/dev/null || true
pkill -KILL -f "launch_raspi_mp4_loop_window.sh" 2>/dev/null || true

pkill -KILL -f "mp4_rx_loop_6002.sh" 2>/dev/null || true
pkill -KILL -f "gst-launch-1.0.*udpsrc.*6002" 2>/dev/null || true
pkill -KILL -f "gst-launch-1.0.*6002" 2>/dev/null || true

pkill -KILL -f "rx_camera_rtp_yolox_realtime_pipe.py" 2>/dev/null || true
pkill -KILL -f "rx_camera_rtp_yolox_realtime.py" 2>/dev/null || true
pkill -KILL -f "rx_camera_rtp_yolox_pipe.py" 2>/dev/null || true
pkill -KILL -f "rx_camera_rtp_yolox.py" 2>/dev/null || true

pkill -KILL -f "csi_live_predictor_from_stream.py" 2>/dev/null || true
pkill -KILL -f "ssh.*stream_csi_stdout_05s_ap13.sh" 2>/dev/null || true

pkill -KILL -f "zone_loop_patrol_v3.py" 2>/dev/null || true
pkill -KILL -f "slam_toolbox" 2>/dev/null || true
pkill -KILL -f "rviz2" 2>/dev/null || true
pkill -KILL -f "ros2 launch.*slam" 2>/dev/null || true

pkill -KILL -f "throughput_local_rx_tk_smooth.py" 2>/dev/null || true
pkill -KILL -f "throughput_local_rx_tk.py" 2>/dev/null || true
pkill -KILL -f "iperf3 -s" 2>/dev/null || true

# Puertos locales relevantes.
sudo fuser -k 5201/tcp 2>/dev/null || true
sudo fuser -k 5201/udp 2>/dev/null || true
sudo fuser -k 6000/udp 2>/dev/null || true
sudo fuser -k 6002/udp 2>/dev/null || true

log "Parando sender/loops en Raspi ${RASPI_HOST}..."

ssh -o ConnectTimeout=5 "${RASPI_USER}@${RASPI_HOST}" '
pkill -9 -f "raspi_60ghz_sender.sh" 2>/dev/null || true
pkill -9 -f "start_mp4_loop_clean.sh" 2>/dev/null || true
pkill -9 -f "mp4_loop_tx_6002.sh" 2>/dev/null || true
pkill -9 -f "iperf_client_loop.sh" 2>/dev/null || true
pkill -9 -f "gst-launch-1.0" 2>/dev/null || true
pkill -9 -f "iperf3" 2>/dev/null || true
rm -f /home/system/raspi_60ghz_demo/logs/*.pid 2>/dev/null || true
rm -f "/home/${RASPI_USER:-nextnet}/raspi_60ghz_demo/logs/"*.pid 2>/dev/null || true
echo RASPI_DEMO_STOPPED
' 2>/dev/null || warn "No pude parar Raspi por ${RASPI_HOST}"

log "Parando CSI en STA .12 vía Raspi WiFi..."

ssh -o ConnectTimeout=8 "${RASPI_USER}@${RASPI_MGMT_HOST}" "
ssh -oHostKeyAlgorithms=+ssh-rsa \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    root@${STA_HOST} '
for pid in \$(ps w | grep -E \"stream_csi|vendor recv|iw dev wlan0 vendor\" | grep -v grep | awk \"{print \\\$1}\"); do
  echo kill CSI pid=\$pid
  kill -9 \$pid 2>/dev/null || true
done

killall -9 iw 2>/dev/null || true
pkill -9 -f stream_csi 2>/dev/null || true
pkill -9 -f vendor 2>/dev/null || true
rm -rf /tmp/csi_stdout_05s_ap13.lock
rm -f /tmp/csi_test_one.txt /tmp/csi_test_one.err
echo STA12_CSI_STOPPED
'
" 2>/dev/null || warn "No pude parar CSI en STA .12"

log "Procesos restantes relevantes en Spark:"
ps -ef | grep -Ei "isaac|docker|rx_camera_rtp_yolox|mp4_rx_loop|csi_live_predictor|stream_csi|zone_loop|slam_toolbox|rviz2|gst-launch|iperf3|throughput|launch_r6" | grep -v grep || true

log "Docker Isaac restante:"
docker ps --format '{{.ID}} {{.Names}} {{.Image}}' 2>/dev/null | grep -Ei 'isaac|omniverse|nvcr.io/nvidia/isaac' || true

log "Procesos restantes relevantes en Raspi:"
ssh -o ConnectTimeout=5 "${RASPI_USER}@${RASPI_HOST}" '
ps -eo pid,ppid,etime,cmd | grep -Ei "start_mp4_loop_clean|mp4_loop_tx_6002|raspi_60ghz_sender|gst-launch|iperf_client_loop|iperf3|v4l2src|filesrc" | grep -v grep || true
' 2>/dev/null || true

log "STOP completado."
