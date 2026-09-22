#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# FULL DEMO STOPPER - CURRENT STABLE STACK
# Portable: paths are derived from this script location.
# Stops Isaac Sim + Go2/ROS + validated 60GHz stack
# + MP4 RX + CSI + YOLO USB + iperf + throughput plot
# ============================================================

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GO2_ROOT="${SCRIPT_DIR}"
ROS_WS="${GO2_ROOT}/ros2_ws"

LOCAL_IFACE="enP7s7"
LOCAL_VIDEO_IP="192.168.1.170/24"
LOCAL_VIDEO_IP_PLAIN="192.168.1.170"
AP_HOST="192.168.1.13"
STA_HOST="192.168.1.12"
AP_USER="root"
STA_USER="root"

SSH_OPTS=(
  -o BatchMode=yes
  -o ConnectTimeout=5
  -o HostKeyAlgorithms=+ssh-rsa
  -o PubkeyAcceptedAlgorithms=+ssh-rsa
  -o StrictHostKeyChecking=accept-new
)

log() { echo -e "\033[1;32m[stop-full-demo]\033[0m $*"; }
warn() { echo -e "\033[1;33m[stop-full-demo][WARN]\033[0m $*" >&2; }

kill_soft() { pkill -TERM -f "$1" 2>/dev/null || true; }
kill_hard() { pkill -KILL -f "$1" 2>/dev/null || true; }

ssh_clean() {
  local host="$1"
  local user="$2"
  local label="$3"
  local body="$4"

  log "Limpiando ${label} (${host})..."
  if ssh "${SSH_OPTS[@]}" "${user}@${host}" "sh -s" <<< "$body"; then
    log "${label} limpio."
  else
    warn "No se pudo limpiar ${label} por SSH."
  fi
}

stop_local_new_stack() {
  log "Parando stack local nuevo: vídeo/YOLO/CSI/iperf/throughput..."

  # Current validated stack
  kill_soft "run_full_demo_current.sh"
  kill_soft "demo_60ghz_video_iperf_csi_yolo_usb.sh"
  kill_soft "video_file_tx_rx_yolo_usb.py"
  kill_soft "video_file_tx_rx_only.py"
  kill_soft "throughput_live_tk.py"
  kill_soft "throughput_live_plot.py"
  kill_soft "csi_live_predictor_from_stream.py"
  kill_soft "gst-launch-1.0"
  kill_soft "udpsink"
  kill_soft "udpsrc"
  kill_soft "rtph264"
  kill_soft "x264enc"
  kill_soft "fpsdisplaysink"
  kill_soft "avdec_h264"
  kill_soft "autovideosink"
  kill_soft "iperf3"
  kill_soft "tcpdump.*5000"
  kill_soft "tcpdump.*5002"

  # Previous dashboard stack compatibility
  kill_soft "video_60ghz_dashboard_csi_yolo_combined.py"
  kill_soft "video_60ghz_dashboard_csi_yolo.py"
  kill_soft "video_60ghz_dashboard_csi.py"
  kill_soft "video_60ghz_dashboard.py"
  kill_soft "realtime_csi_monitor.py"

  sleep 2

  kill_hard "demo_60ghz_video_iperf_csi_yolo_usb.sh"
  kill_hard "video_file_tx_rx_yolo_usb.py"
  kill_hard "video_file_tx_rx_only.py"
  kill_hard "throughput_live_tk.py"
  kill_hard "throughput_live_plot.py"
  kill_hard "csi_live_predictor_from_stream.py"
  kill_hard "gst-launch-1.0"
  kill_hard "iperf3"

  sudo fuser -k 5000/udp 2>/dev/null || true
  sudo fuser -k 5002/udp 2>/dev/null || true
  sudo fuser -k 5201/tcp 2>/dev/null || true
  sudo fuser -k 5201/udp 2>/dev/null || true
  sudo fuser -k 5202/tcp 2>/dev/null || true
  sudo fuser -k 5202/udp 2>/dev/null || true
  sudo fuser -k 5203/tcp 2>/dev/null || true
  sudo fuser -k 5203/udp 2>/dev/null || true
  sudo fuser -k 5204/tcp 2>/dev/null || true
  sudo fuser -k 5204/udp 2>/dev/null || true
  sudo fuser -k /dev/video6 2>/dev/null || true
}

stop_ros_go2() {
  log "Parando ROS/Go2/SLAM/Zone Loop..."

  for pat in \
    "go2_slam_live_visual.launch.py" \
    "slam_live_viewer_detector.py" \
    "slam_live_viewer_detector" \
    "slam_novelty_detector.py" \
    "go2_scan_filter.py" \
    "cmd_vel_out_relay.py" \
    "cmd_vel_relay.py" \
    "zone_loop_patrol_v3.py" \
    "zone_loop_patrol" \
    "slam_toolbox" \
    "async_slam_toolbox_node" \
    "sync_slam_toolbox_node" \
    "pointcloud_to_laserscan" \
    "go2_pointcloud_to_laserscan" \
    "lidar_to_pointcloud" \
    "pointcloud_aggregator" \
    "lidar_novelty_guard.py" \
    "state_bridge" \
    "robot.launch.py" \
    "go2_robot_sdk" \
    "go2_driver_node" \
    "go2_driver" \
    "robot_state_publisher" \
    "rviz2" \
    "foxglove_bridge" \
    "nav2" \
    "docking_server"; do
    kill_soft "$pat"
  done

  sleep 2

  for pat in \
    "go2_slam_live_visual.launch.py" \
    "slam_live_viewer_detector.py" \
    "zone_loop_patrol_v3.py" \
    "slam_toolbox" \
    "go2_robot_sdk" \
    "go2_driver" \
    "robot_state_publisher" \
    "rviz2" \
    "foxglove_bridge"; do
    kill_hard "$pat"
  done

  pkill -TERM -f "ros2" 2>/dev/null || true
  sleep 1
  pkill -KILL -f "ros2" 2>/dev/null || true

  ros2 daemon stop 2>/dev/null || true
  sleep 1
  ros2 daemon start 2>/dev/null || true
}

stop_docker_isaac() {
  log "Parando Isaac Sim Docker..."
  docker rm -f isaac-sim-gui 2>/dev/null || true
  docker rm -f isaac-sim 2>/dev/null || true

  local ids
  ids="$(docker ps -aq --filter ancestor=nvcr.io/nvidia/isaac-sim:5.1.0 2>/dev/null || true)"
  if [[ -n "${ids}" ]]; then
    docker rm -f ${ids} 2>/dev/null || true
  fi
}

clean_ap() {
  ssh_clean "$AP_HOST" "$AP_USER" "AP" '
set -u

echo "[AP] stopping radio/load processes"
killall -9 iperf3 2>/dev/null || true
killall -9 tcpdump 2>/dev/null || true
killall -9 hostapd 2>/dev/null || true
killall -9 wpa_supplicant 2>/dev/null || true
killall -9 iw 2>/dev/null || true
pkill -9 -f "iperf3" 2>/dev/null || true
pkill -9 -f "tcpdump" 2>/dev/null || true
pkill -9 -f "iw dev wlan0 vendor" 2>/dev/null || true
pkill -9 -f "vendor recv" 2>/dev/null || true

iptables -t nat -F 2>/dev/null || true
iptables -F 2>/dev/null || true
sysctl -w net.ipv4.ip_forward=0 2>/dev/null || true

ip addr flush dev wlan0 2>/dev/null || true
ip link set wlan0 down 2>/dev/null || true

echo "[AP] remaining relevant processes:"
ps w | grep -E "iperf3|tcpdump|hostapd|wpa_supplicant|vendor recv|iw dev wlan0 vendor" | grep -v grep || true
echo "[AP] OK"
'
}

clean_sta() {
  ssh_clean "$STA_HOST" "$STA_USER" "STA" '
set -u

echo "[STA] stopping radio/load/CSI processes"
for pid in $(ps w | grep -E "stream_csi|vendor recv|iw dev wlan0 vendor|id_rsa_dropbear|cat >>|iperf3|tcpdump" | grep -v grep | awk "{print \$1}"); do
  echo "kill -9 $pid"
  kill -9 "$pid" 2>/dev/null || true
done

killall -9 iw 2>/dev/null || true
killall -9 iperf3 2>/dev/null || true
killall -9 tcpdump 2>/dev/null || true
killall -9 wpa_supplicant 2>/dev/null || true
killall -9 hostapd 2>/dev/null || true
killall -9 ssh 2>/dev/null || true
killall -9 dbclient 2>/dev/null || true

pkill -9 -f "stream_csi" 2>/dev/null || true
pkill -9 -f "live_csi_stream" 2>/dev/null || true
pkill -9 -f "vendor recv" 2>/dev/null || true
pkill -9 -f "iw dev wlan0 vendor" 2>/dev/null || true
pkill -9 -f "id_rsa_dropbear" 2>/dev/null || true
pkill -9 -f "cat >>" 2>/dev/null || true

rm -rf /tmp/csi_05s_single.lock /tmp/csi_*.lock
rm -f /tmp/csi_05s_single_local.txt /tmp/csi_05s_single.out /tmp/csi_oneshot.out
rm -f /tmp/csi_stream_*.pid /tmp/csi_stream_*.out /tmp/csi_*local*.txt

iptables -t nat -F 2>/dev/null || true
iptables -F 2>/dev/null || true
sysctl -w net.ipv4.ip_forward=0 2>/dev/null || true

ip addr flush dev wlan0 2>/dev/null || true
ip link set wlan0 down 2>/dev/null || true

echo "[STA] remaining relevant processes:"
ps w | grep -E "stream_csi|vendor recv|iw dev wlan0 vendor|id_rsa_dropbear|cat >>|iperf3|tcpdump|wpa_supplicant" | grep -v grep || true
echo "[STA] OK"
'
}

clean_local_network() {
  log "Limpiando IP local ${LOCAL_VIDEO_IP} en ${LOCAL_IFACE}..."
  if ip -4 addr show dev "$LOCAL_IFACE" | grep -q "${LOCAL_VIDEO_IP_PLAIN}/24"; then
    sudo ip addr del "$LOCAL_VIDEO_IP" dev "$LOCAL_IFACE" 2>/dev/null || true
    log "IP local eliminada."
  else
    log "IP local no estaba configurada."
  fi
}

close_demo_terminals() {
  log "Cerrando terminales de demo..."

  for pat in \
    "T1 - Isaac Sim 5.1" \
    "T2 - Go2 SLAM Live Visual" \
    "T3 - 60GHz Video CSI YOLO iperf" \
    "T3 - CSI" \
    "T4 - Throughput Live Plot" \
    "T4 - VIDEO" \
    "T5 - iperf3" \
    "T6 - SSH" \
    "T7 - SSH" \
    "T8 - Zone Loop Patrol" \
    "60 GHz Live Throughput" \
    "usb-yolo" \
    "proceso terminado. Pulsa ENTER para cerrar"; do
    pkill -TERM -f "$pat" 2>/dev/null || true
  done

  pkill -TERM -f "ssh .*root@192.168.1.13" 2>/dev/null || true
  pkill -TERM -f "ssh .*root@192.168.1.12" 2>/dev/null || true

  sleep 1

  for pat in \
    "T3 - 60GHz Video CSI YOLO iperf" \
    "T4 - Throughput Live Plot" \
    "T4 - VIDEO" \
    "T5 - iperf3" \
    "proceso terminado. Pulsa ENTER para cerrar"; do
    pkill -KILL -f "$pat" 2>/dev/null || true
  done
}

show_remaining() {
  log "Procesos restantes relevantes en Spark:"
  ps -eo pid,ppid,pcpu,pmem,cmd | grep -E "video_file_tx|demo_60ghz|throughput_live|csi_live|gst-launch|iperf3|tcpdump|h264|yolo|video6|go2_slam|zone_loop|slam_toolbox|go2_driver|isaac" | grep -v grep || true

  log "Cámara ocupada:"
  fuser -v /dev/video6 2>/dev/null || true

  log "ROS state:"
  if command -v ros2 >/dev/null 2>&1; then
    (
      cd "${ROS_WS}" 2>/dev/null || true
      source /opt/ros/jazzy/setup.bash 2>/dev/null || true
      source install/setup.bash 2>/dev/null || true
      timeout 5 ros2 node list 2>/dev/null || true
    )
  fi
}

main() {
  log "Parando demo completa actual..."
  stop_local_new_stack
  stop_ros_go2
  stop_docker_isaac
  clean_ap
  clean_sta
  clean_local_network
  close_demo_terminals
  show_remaining
  log "Limpieza terminada."
}

main "$@"
