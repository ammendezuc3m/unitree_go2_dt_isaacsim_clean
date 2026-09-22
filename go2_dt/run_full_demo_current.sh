#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# FULL DEMO LAUNCHER - CURRENT STABLE STACK
# /home/nextnet/AlbertoDir/go2_dt/run_full_demo_current.sh
# Isaac Sim + Go2 SLAM + validated 60 GHz stack
# + MP4 RX video 20 Mbps + iperf UDP 180 Mbps
# + external CSI + local USB YOLO + live throughput plot
# + zone loop patrol
# ============================================================

USER_HOME="${HOME}"
GO2_ROOT="${USER_HOME}/AlbertoDir/go2_dt"
ROS_WS="${GO2_ROOT}/ros2_ws"
REPO_ROOT="$(cd "${GO2_ROOT}/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"

# Validated radio/video/CSI/YOLO/iperf scripts
RADIO_STACK_SH="${GO2_ROOT}/demo_60ghz_video_iperf_csi_yolo_usb.sh"
VIDEO_YOLO_PY="${GO2_ROOT}/video_file_tx_rx_yolo_usb.py"
THROUGHPUT_TK_PY="${GO2_ROOT}/throughput_live_tk.py"

# ROS / Isaac / robot
GO2_SLAM_LIVE_LAUNCH="${ROS_WS}/launch/go2_slam_live_visual.launch.py"
ZONE_LOOP_SCRIPT="${ROS_WS}/zone_loop_patrol_v3.py"
PHASE_LAP_FILE="${ROS_WS}/phase_trained_lap.json"
LIDAR_STATE_PATH="${ROS_WS}/lidar_outputs/live_lidar_state.json"

ROBOT_IP="192.168.12.1"
CONN_TYPE="webrtc"

# Network
LOCAL_IFACE="enP7s7"
LOCAL_VIDEO_IP="192.168.1.170/24"
LOCAL_VIDEO_IP_PLAIN="192.168.1.170"
AP_HOST="192.168.1.13"
STA_HOST="192.168.1.12"
AP_USER="root"
STA_USER="root"

# Throughput plot settings validated by demo
THROUGHPUT_SCALE_MAX="260"
THROUGHPUT_SAMPLES="240"
THROUGHPUT_INTERVAL="1"
THROUGHPUT_SMOOTH_SAMPLES="5"

# Zone loop patrol
ZONE_LOOP_CAMERA_INDEX="0"
ZONE_LOOP_APRILTAG_ID="0"
ZONE_LOOP_CMD_TOPIC="/cmd_vel_out"
ZONE_LOOP_REPEAT_AUTO="true"
ZONE_LOOP_AUTO_ALIGN_ENABLED="true"
ZONE_LOOP_ENABLE_CAMERA_STOP="true"
ZONE_LOOP_ENABLE_MMWAVE_STOP="true"
ZONE_LOOP_ENABLE_LIDAR_STOP="true"
ZONE_LOOP_TAG_TOL_X_M="0.03"
ZONE_LOOP_TAG_TOL_Y_M="0.03"
ZONE_LOOP_TAG_TOL_YAW_DEG="1.5"
ZONE_LOOP_PERSON_CONFIRM_SEC="0.5"
ZONE_LOOP_CLEAR_CONFIRM_SEC="1.5"

SSH_OPTS=(
  -o BatchMode=yes
  -o ConnectTimeout=5
  -o HostKeyAlgorithms=+ssh-rsa
  -o PubkeyAcceptedAlgorithms=+ssh-rsa
  -o StrictHostKeyChecking=accept-new
)

log() { echo -e "\033[1;32m[full-demo]\033[0m $*"; }
warn() { echo -e "\033[1;33m[full-demo][WARN]\033[0m $*" >&2; }
err() { echo -e "\033[1;31m[full-demo][ERROR]\033[0m $*" >&2; }

check_file() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    err "No existe: $path"
    exit 1
  fi
}

check_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    err "No se encontró comando requerido: $cmd"
    exit 1
  fi
}

check_graphical_terminal() {
  if command -v gnome-terminal >/dev/null 2>&1 || \
     command -v xterm >/dev/null 2>&1 || \
     command -v konsole >/dev/null 2>&1; then
    return 0
  fi

  err "No encuentro un terminal gráfico compatible (gnome-terminal, xterm o konsole)."
  exit 1
}

open_terminal() {
  local title="$1"
  local command="$2"

  if command -v gnome-terminal >/dev/null 2>&1; then
    gnome-terminal --title="$title" -- bash -lc "$command; echo; echo '[${title}] proceso terminado. Pulsa ENTER para cerrar...'; read -r" &
  elif command -v xterm >/dev/null 2>&1; then
    xterm -T "$title" -e bash -lc "$command; echo; echo '[${title}] proceso terminado. Pulsa ENTER para cerrar...'; read -r" &
  elif command -v konsole >/dev/null 2>&1; then
    konsole --new-tab --workdir "$GO2_ROOT" -p tabtitle="$title" -e bash -lc "$command; echo; echo '[${title}] proceso terminado. Pulsa ENTER para cerrar...'; read -r" &
  else
    err "No encuentro gnome-terminal, xterm ni konsole."
    exit 1
  fi
}

wait_for_ssh() {
  local host="$1"
  local user="$2"
  local label="$3"

  log "Comprobando SSH en ${label} (${host})..."

  for i in $(seq 1 20); do
    if ssh "${SSH_OPTS[@]}" "${user}@${host}" "echo ok" >/dev/null 2>&1; then
      log "SSH OK en ${label}"
      return 0
    fi
    warn "SSH no disponible en ${label}. Intento ${i}/20"
    sleep 1
  done

  err "No se pudo conectar por SSH a ${label} (${host})"
  exit 1
}

configure_local_ip_and_routes() {
  log "Configurando IP local ${LOCAL_VIDEO_IP} en ${LOCAL_IFACE}..."

  if ip -4 addr show dev "$LOCAL_IFACE" | grep -q "${LOCAL_VIDEO_IP_PLAIN}/24"; then
    log "IP local ya configurada."
  else
    sudo ip addr add "$LOCAL_VIDEO_IP" dev "$LOCAL_IFACE"
    log "IP local añadida."
  fi

  sudo ip route replace "${AP_HOST}/32" dev "$LOCAL_IFACE" src "$LOCAL_VIDEO_IP_PLAIN" metric 1
  sudo ip route replace "${STA_HOST}/32" dev "$LOCAL_IFACE" src "$LOCAL_VIDEO_IP_PLAIN" metric 1
  sudo ip route flush cache

  log "Ruta AP:"
  ip route get "$AP_HOST" || true
  log "Ruta STA:"
  ip route get "$STA_HOST" || true
}

cleanup_stale_local_processes() {
  log "Limpieza local inicial de procesos antiguos..."

  # New validated stack
  pkill -TERM -f "demo_60ghz_video_iperf_csi_yolo_usb.sh" 2>/dev/null || true
  pkill -TERM -f "video_file_tx_rx_yolo_usb.py" 2>/dev/null || true
  pkill -TERM -f "video_file_tx_rx_only.py" 2>/dev/null || true
  pkill -TERM -f "throughput_live_tk.py" 2>/dev/null || true
  pkill -TERM -f "throughput_live_plot.py" 2>/dev/null || true
  pkill -TERM -f "csi_live_predictor_from_stream.py" 2>/dev/null || true
  pkill -TERM -f "gst-launch-1.0" 2>/dev/null || true
  pkill -TERM -f "iperf3" 2>/dev/null || true

  # Previous video/dashboard stack
  pkill -TERM -f "video_60ghz_dashboard_csi_yolo_combined.py" 2>/dev/null || true
  pkill -TERM -f "video_60ghz_dashboard" 2>/dev/null || true

  sleep 1

  pkill -KILL -f "demo_60ghz_video_iperf_csi_yolo_usb.sh" 2>/dev/null || true
  pkill -KILL -f "video_file_tx_rx_yolo_usb.py" 2>/dev/null || true
  pkill -KILL -f "video_file_tx_rx_only.py" 2>/dev/null || true
  pkill -KILL -f "throughput_live_tk.py" 2>/dev/null || true
  pkill -KILL -f "throughput_live_plot.py" 2>/dev/null || true
  pkill -KILL -f "csi_live_predictor_from_stream.py" 2>/dev/null || true
  pkill -KILL -f "gst-launch-1.0" 2>/dev/null || true
  pkill -KILL -f "iperf3" 2>/dev/null || true

  sudo fuser -k 5000/udp 2>/dev/null || true
  sudo fuser -k 5002/udp 2>/dev/null || true
  sudo fuser -k 5201/tcp 2>/dev/null || true
  sudo fuser -k 5201/udp 2>/dev/null || true
  sudo fuser -k /dev/video6 2>/dev/null || true

  log "Limpieza local inicial completada."
}

preflight() {
  log "Preflight..."

  check_cmd ssh
  check_cmd ip
  check_cmd python3
  check_cmd docker
  check_graphical_terminal

  check_file "$RADIO_STACK_SH"
  check_file "$VIDEO_YOLO_PY"
  check_file "$THROUGHPUT_TK_PY"
  check_file "$GO2_SLAM_LIVE_LAUNCH"
  check_file "$ZONE_LOOP_SCRIPT"
  check_file "$PHASE_LAP_FILE"
  check_file "${ROS_WS}/install/setup.bash"
  check_file "${VENV_DIR}/bin/activate"
  check_file "${VENV_DIR}/bin/python"

  sudo -v

  if [[ -z "${DISPLAY:-}" ]]; then
    err "DISPLAY no está definido. La demo necesita sesión gráfica/X11."
    exit 1
  fi

  if [[ -z "${XAUTHORITY:-}" ]]; then
    warn "XAUTHORITY no está definido. Intentando usar ~/.Xauthority"
    export XAUTHORITY="${HOME}/.Xauthority"
  fi

  "${VENV_DIR}/bin/python" -m py_compile "$VIDEO_YOLO_PY"
  "${VENV_DIR}/bin/python" -m py_compile "$THROUGHPUT_TK_PY"
  bash -n "$RADIO_STACK_SH"
}

launch_isaac() {
  log "Lanzando Isaac Sim..."

  xhost +local:docker >/dev/null 2>&1 || true

  open_terminal "T1 - Isaac Sim 5.1" "
set -e
xhost +local:docker >/dev/null 2>&1 || true

docker rm -f isaac-sim-gui 2>/dev/null || true
docker rm -f isaac-sim 2>/dev/null || true

docker run --rm -it \\
  --name isaac-sim-gui \\
  --gpus all \\
  --network=host \\
  --ipc=host \\
  --ulimit memlock=-1 \\
  --ulimit stack=67108864 \\
  -e ACCEPT_EULA=Y \\
  -e PRIVACY_CONSENT=Y \\
  -e DISPLAY=\$DISPLAY \\
  -e XAUTHORITY=\$XAUTHORITY \\
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \\
  -v \$XAUTHORITY:\$XAUTHORITY:rw \\
  -v ~/AlbertoDir/isaac51/cache/main/ov:/home/ubuntu/.cache/ov:rw \\
  -v ~/AlbertoDir/isaac51/cache/main/warp:/home/ubuntu/.cache/warp:rw \\
  -v ~/AlbertoDir/isaac51/cache/computecache:/home/ubuntu/.nv/ComputeCache:rw \\
  -v ~/AlbertoDir/isaac51/config:/home/ubuntu/.nvidia-omniverse/config:rw \\
  -v ~/AlbertoDir/isaac51/data/documents:/home/ubuntu/Documents:rw \\
  -v ~/AlbertoDir/isaac51/data/Kit:/home/ubuntu/.local/share/ov/data/Kit:rw \\
  -v ~/AlbertoDir/isaac51/logs:/home/ubuntu/.nvidia-omniverse/logs:rw \\
  -v ~/AlbertoDir:/workspace:rw \\
  --entrypoint /bin/bash \\
  nvcr.io/nvidia/isaac-sim:5.1.0 \\
  -lc 'cd /isaac-sim && ./runapp.sh'
"
}

launch_go2_slam_live_visual() {
  log "Lanzando Go2 SDK + SLAM live visual..."

  open_terminal "T2 - Go2 SLAM Live Visual" "
set -e
cd '${ROS_WS}'
export ROBOT_IP='${ROBOT_IP}'
export CONN_TYPE='${CONN_TYPE}'
source /opt/ros/jazzy/setup.bash
source '${VENV_DIR}/bin/activate'
source install/setup.bash
ros2 launch '${GO2_SLAM_LIVE_LAUNCH}'
"
}

launch_radio_video_csi_yolo_iperf() {
  log "Lanzando stack validado 60 GHz + vídeo + CSI + YOLO + iperf..."

  open_terminal "T3 - 60GHz Video CSI YOLO iperf" "
set -e
cd '${GO2_ROOT}'
source '${VENV_DIR}/bin/activate'
./$(basename "$RADIO_STACK_SH")
"
}

launch_throughput_plot() {
  log "Lanzando gráfica live throughput..."

  open_terminal "T4 - Throughput Live Plot" "
set -e
cd '${GO2_ROOT}'
source '${VENV_DIR}/bin/activate'
python '${THROUGHPUT_TK_PY}' \\
  --local-iface '${LOCAL_IFACE}' \\
  --sta-host '${STA_HOST}' \\
  --scale-max '${THROUGHPUT_SCALE_MAX}' \\
  --samples '${THROUGHPUT_SAMPLES}' \\
  --interval '${THROUGHPUT_INTERVAL}' \\
  --smooth-samples '${THROUGHPUT_SMOOTH_SAMPLES}'
"
}

launch_zone_loop() {
  log "Lanzando zone loop patrol..."

  open_terminal "T8 - Zone Loop Patrol" "
set -e
cd '${ROS_WS}'
source /opt/ros/jazzy/setup.bash
source '${VENV_DIR}/bin/activate'
source install/setup.bash

python '${ZONE_LOOP_SCRIPT}' \\
  --ros-args \\
  -p mode:=auto \\
  -p camera_index:=${ZONE_LOOP_CAMERA_INDEX} \\
  -p apriltag_id:=${ZONE_LOOP_APRILTAG_ID} \\
  -p cmd_topic:='${ZONE_LOOP_CMD_TOPIC}' \\
  -p input_file:='${PHASE_LAP_FILE}' \\
  -p repeat_auto:=${ZONE_LOOP_REPEAT_AUTO} \\
  -p auto_align_enabled:=${ZONE_LOOP_AUTO_ALIGN_ENABLED} \\
  -p direct_align_enabled:=true \\
  -p position_first:=true \\
  -p camera_align_position_gain:=0.45 \\
  -p camera_align_min_xy_move_sec:=0.10 \\
  -p camera_align_max_xy_move_sec:=0.45 \\
  -p camera_align_min_yaw_move_sec:=0.10 \\
  -p camera_align_max_yaw_move_sec:=0.35 \\
  -p camera_align_hard_stop_sec:=0.8 \\
  -p camera_align_linear_x_speed_m_s:=0.055 \\
  -p camera_align_linear_y_speed_m_s:=0.045 \\
  -p camera_align_angular_speed_rad_s:=0.12 \\
  -p cmd_x_sign:=1.0 \\
  -p cmd_y_sign:=1.0 \\
  -p cmd_w_sign:=-1.0 \\
  -p enable_camera_stop:=${ZONE_LOOP_ENABLE_CAMERA_STOP} \\
  -p enable_mmwave_stop:=${ZONE_LOOP_ENABLE_MMWAVE_STOP} \\
  -p enable_lidar_stop:=${ZONE_LOOP_ENABLE_LIDAR_STOP} \\
  -p lidar_state_path:='${LIDAR_STATE_PATH}' \\
  -p tag_tol_x_m:=${ZONE_LOOP_TAG_TOL_X_M} \\
  -p tag_tol_y_m:=${ZONE_LOOP_TAG_TOL_Y_M} \\
  -p tag_tol_yaw_deg:=${ZONE_LOOP_TAG_TOL_YAW_DEG} \\
  -p person_confirm_sec:=${ZONE_LOOP_PERSON_CONFIRM_SEC} \\
  -p clear_confirm_sec:=${ZONE_LOOP_CLEAR_CONFIRM_SEC}
"
}

main() {
  log "Iniciando FULL DEMO con stack actual validado."

  cleanup_stale_local_processes
  preflight
  configure_local_ip_and_routes

  wait_for_ssh "$AP_HOST" "$AP_USER" "AP"
  wait_for_ssh "$STA_HOST" "$STA_USER" "STA"

  launch_isaac
  sleep 4

  launch_go2_slam_live_visual
  sleep 8

  launch_radio_video_csi_yolo_iperf

  # Dejamos margen para que el script validado configure radio, arranque CSI/video/iperf.
  sleep 25

  launch_throughput_plot
  sleep 2

  launch_zone_loop

  log "Todo lanzado."
  log "Ventanas esperadas:"
  log "  T1 Isaac Sim"
  log "  T2 Go2 SLAM Live Visual"
  log "  T3 launcher 60GHz validado + subventanas CSI/video/iperf/YOLO"
  log "  T4 Throughput Live Plot"
  log "  T8 Zone Loop Patrol"
  log ""
  log "Stack radio validado: MP4 20 Mbps + iperf UDP 180 Mbps + CSI 0.5s + YOLO USB RealSense."
}

main "$@"
