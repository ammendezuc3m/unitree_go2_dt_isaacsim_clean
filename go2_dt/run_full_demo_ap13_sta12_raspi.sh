#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# FULL DEMO - AP13/STA12 RASPI STACK
# Portable: host paths are derived from this script location.
# Isaac Sim + Go2 SLAM
# + Raspi -> AP .13 -> 60GHz -> STA .12 -> Spark
# + MP4 RX + camera YOLO RX + iperf + local RX throughput
# + Zone Loop Patrol
#
# IMPORTANTE:
# Este launcher NO configura antenas.
# Antes debe ejecutarse el setup:
#   tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link.sh
# con todos los cables al switch.
#
# Para ejecutar este run:
#   cables finales:
#     Raspi -> AP .13
#     STA .12 -> Spark
# ============================================================

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GO2_ROOT="${SCRIPT_DIR}"
REPO_ROOT="$(cd "${GO2_ROOT}/.." && pwd)"
ROS_WS="${GO2_ROOT}/ros2_ws"
VENV_DIR="${REPO_ROOT}/.venv"
ISAAC_DATA_ROOT="${REPO_ROOT}/.isaac51"
CSI_LIVE_PREDICTOR="${GO2_ROOT}/csi_live_predictor_from_stream.py"
CSI_VOTE_THRESHOLD="2"
CSI_VOTE_WINDOW="3"
CSI_CONFIRM_COUNT="1"
CSI_PREDICTIONS_CSV="${GO2_ROOT}/csi_dog_dataset_20210421_181125/analysis_outputs/live_predictions_external_stream.csv"
CSI_STATE_JSON="${GO2_ROOT}/csi_dog_dataset_20210421_181125/analysis_outputs/live_prediction_state.json"
CSI_STREAM_FILE="${GO2_ROOT}/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt"
CSI_MODEL="${GO2_ROOT}/csi_dog_dataset_20210421_181125/analysis_outputs/csi_empty_person_model_20260526_all_1_2200.joblib"

# Nuevo stack Raspi/AP13/STA12 sin setup de antenas.
RASPI_STACK_SH="${GO2_ROOT}/tests_experiments/raspi_60ghz_ap13_sta12/run_demo_ap13_sta12_no_setup.sh"
RASPI_STOP_SH="${GO2_ROOT}/tests_experiments/raspi_60ghz_ap13_sta12/stop_ap13_sta12_pipeline.sh"

# ROS / Isaac / robot
GO2_SLAM_LIVE_LAUNCH="${ROS_WS}/launch/go2_slam_live_visual.launch.py"
ZONE_LOOP_SCRIPT="${ROS_WS}/zone_loop_patrol_v3.py"
PHASE_LAP_FILE="${ROS_WS}/phase_trained_lap.json"
LIDAR_STATE_PATH="${ROS_WS}/lidar_outputs/live_lidar_state.json"

ROBOT_IP="192.168.12.1"
CONN_TYPE="webrtc"

# Red final AP13/STA12
LOCAL_IFACE="enP7s7"
SPARK_IP="172.16.12.170"
RASPI_HOST="172.16.13.100"
RASPI_USER="${RASPI_USER:-nextnet}"
RASPI_RUNTIME_DIR="${RASPI_RUNTIME_DIR:-${RASPI_RUNTIME_DIR}}"

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

# Carga demo
MP4_FILE="${MP4_FILE:-${RASPI_RUNTIME_DIR}/golden_test.mp4}"
IPERF_BITRATE="${IPERF_BITRATE:-400M}"
THROUGHPUT_SCALE_MAX="${THROUGHPUT_SCALE_MAX:-500}"
THROUGHPUT_SMOOTH_SAMPLES="${THROUGHPUT_SMOOTH_SAMPLES:-30}"

# Zone loop patrol
ZONE_LOOP_CAMERA_INDEX="${ZONE_LOOP_CAMERA_INDEX:-0}"
ZONE_LOOP_APRILTAG_ID="0"
# Camera assignment for current demo:
#   /dev/video0 -> YOLO local
#   /dev/video1 -> AprilTag
YOLO_CAMERA_DEVICE="${YOLO_CAMERA_DEVICE:-/dev/video0}"
APRILTAG_CAMERA_DEVICE="${APRILTAG_CAMERA_DEVICE:-/dev/video0}"
ENABLE_LOCAL_YOLO="${ENABLE_LOCAL_YOLO:-1}"

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

log() { echo -e "\033[1;32m[full-demo-ap13]\033[0m $*"; }
warn() { echo -e "\033[1;33m[full-demo-ap13][WARN]\033[0m $*" >&2; }
err() { echo -e "\033[1;31m[full-demo-ap13][ERROR]\033[0m $*" >&2; }

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

cleanup_stale_local_processes() {
  log "Limpieza local inicial de procesos antiguos..."

  # Stack viejo
  pkill -TERM -f "demo_60ghz_video_iperf_csi_yolo_usb.sh" 2>/dev/null || true
  pkill -TERM -f "video_file_tx_rx_yolo_usb.py" 2>/dev/null || true
  pkill -TERM -f "video_file_tx_rx_only.py" 2>/dev/null || true
  pkill -TERM -f "throughput_live_tk.py" 2>/dev/null || true
  pkill -TERM -f "throughput_live_plot.py" 2>/dev/null || true
  pkill -TERM -f "csi_live_predictor_from_stream.py" 2>/dev/null || true

  # Stack nuevo Raspi
  pkill -TERM -f "run_demo_ap13_sta12_no_setup.sh" 2>/dev/null || true
  pkill -TERM -f "rx_camera_rtp_yolox_pipe.py" 2>/dev/null || true
  pkill -TERM -f "rx_camera_rtp_yolox_realtime_pipe.py" 2>/dev/null || true
  pkill -TERM -f "throughput_local_rx_tk_smooth.py" 2>/dev/null || true

  # Procesos comunes
  pkill -TERM -f "gst-launch-1.0" 2>/dev/null || true
  pkill -TERM -f "iperf3" 2>/dev/null || true

  sleep 1

  pkill -KILL -f "demo_60ghz_video_iperf_csi_yolo_usb.sh" 2>/dev/null || true
  pkill -KILL -f "run_demo_ap13_sta12_no_setup.sh" 2>/dev/null || true
  pkill -KILL -f "video_file_tx_rx_yolo_usb.py" 2>/dev/null || true
  pkill -KILL -f "video_file_tx_rx_only.py" 2>/dev/null || true
  pkill -KILL -f "throughput_live_tk.py" 2>/dev/null || true
  pkill -KILL -f "throughput_local_rx_tk_smooth.py" 2>/dev/null || true
  pkill -KILL -f "throughput_live_plot.py" 2>/dev/null || true
  pkill -KILL -f "csi_live_predictor_from_stream.py" 2>/dev/null || true
  pkill -KILL -f "rx_camera_rtp_yolox_pipe.py" 2>/dev/null || true
  pkill -KILL -f "rx_camera_rtp_yolox_realtime_pipe.py" 2>/dev/null || true
  pkill -KILL -f "gst-launch-1.0" 2>/dev/null || true
  pkill -KILL -f "iperf3" 2>/dev/null || true

  sudo fuser -k 5000/udp 2>/dev/null || true
  sudo fuser -k 5002/udp 2>/dev/null || true
  sudo fuser -k 5201/tcp 2>/dev/null || true
  sudo fuser -k 5201/udp 2>/dev/null || true
  sudo fuser -k 6000/udp 2>/dev/null || true
  sudo fuser -k 6002/udp 2>/dev/null || true

  log "Limpieza local inicial completada."
}

cleanup_raspi_processes() {
  log "Limpieza inicial en Raspi..."

  ssh -o ConnectTimeout=5 ${RASPI_USER}@"${RASPI_HOST}" '
for pid in $(ps -eo pid,args | awk "/iperf3|gst-launch-1.0|v4l2src|x264enc|raspi_60ghz_sender|while true|mp4-loop|iperf-loop/ && !/awk/ {print \$1}"); do
  kill -9 "$pid" 2>/dev/null || true
done

killall -9 iperf3 2>/dev/null || true
killall -9 gst-launch-1.0 2>/dev/null || true
' || {
    warn "No pude limpiar Raspi por ${RASPI_HOST}; pruebo gestión ${RASPI_MGMT_HOST}"
    ssh -o ConnectTimeout=5 ${RASPI_USER}@"${RASPI_MGMT_HOST}" '
for pid in $(ps -eo pid,args | awk "/iperf3|gst-launch-1.0|v4l2src|x264enc|raspi_60ghz_sender|while true|mp4-loop|iperf-loop/ && !/awk/ {print \$1}"); do
  kill -9 "$pid" 2>/dev/null || true
done

killall -9 iperf3 2>/dev/null || true
killall -9 gst-launch-1.0 2>/dev/null || true
' || true
  }
}

configure_final_local_route() {
  log "Asegurando IP/rutas finales en Spark para AP13/STA12..."

  # En esta topología Spark solo debe tener la IP de lado STA.
  sudo ip addr del 172.16.13.170/24 dev "${LOCAL_IFACE}" 2>/dev/null || true
  sudo ip addr add "${SPARK_IP}/24" dev "${LOCAL_IFACE}" 2>/dev/null || true

  sudo ip route del 172.16.13.0/24 2>/dev/null || true
  sudo ip route replace 172.16.13.0/24 via 172.16.12.1 dev "${LOCAL_IFACE}" src "${SPARK_IP}"

  sudo ip route flush cache 2>/dev/null || true

  log "Ruta Raspi:"
  ip route get "${RASPI_HOST}" || true
}

preflight() {
  log "Preflight..."

  check_cmd ssh
  check_cmd ip
  check_cmd python3
  check_cmd docker
  check_graphical_terminal
  check_cmd gst-launch-1.0

  check_file "$RASPI_STACK_SH"
  check_file "$RASPI_STOP_SH"
  check_file "$GO2_SLAM_LIVE_LAUNCH"
  check_file "$ZONE_LOOP_SCRIPT"
  check_file "$PHASE_LAP_FILE"
  check_file "${ROS_WS}/install/setup.bash"
  check_file "${VENV_DIR}/bin/activate"
  check_file "${VENV_DIR}/bin/python"

  bash -n "$RASPI_STACK_SH"
  bash -n "$RASPI_STOP_SH"
  "${VENV_DIR}/bin/python" -m py_compile "${GO2_ROOT}/tests_experiments/raspi_60ghz/rx_camera_rtp_yolox_realtime_pipe.py"
  "${VENV_DIR}/bin/python" -m py_compile "${GO2_ROOT}/tests_experiments/raspi_60ghz_ap13_sta12/throughput_local_rx_tk_smooth.py"

  sudo -v

  if [[ -z "${DISPLAY:-}" ]]; then
    err "DISPLAY no está definido. La demo necesita sesión gráfica/X11."
    exit 1
  fi

  if [[ -z "${XAUTHORITY:-}" ]]; then
    warn "XAUTHORITY no está definido. Intentando usar ~/.Xauthority"
    export XAUTHORITY="${HOME}/.Xauthority"
  fi
}

check_final_link_ready() {
  log "Comprobando que el setup AP13/STA12 ya está activo..."

  echo
  echo "=== Spark -> Raspi final ==="
  ping -c 5 -W 2 "${RASPI_HOST}"

  echo
  echo "=== SSH Raspi final ==="
  ssh -o ConnectTimeout=5 ${RASPI_USER}@"${RASPI_HOST}" "
echo SSH_OK_RASPI_FINAL
ip route get '${SPARK_IP}'
test -e ${YOLO_CAMERA_DEVICE} && echo "${YOLO_CAMERA_DEVICE} YOLO OK" || echo "${YOLO_CAMERA_DEVICE} YOLO MISSING"; echo "[INFO] AprilTag camera is local on Spark: ${APRILTAG_CAMERA_DEVICE}"
test -x ${RASPI_RUNTIME_DIR}/raspi_60ghz_sender.sh && echo 'sender OK' || echo 'sender MISSING'
"
}

launch_isaac() {
  log "Lanzando Isaac Sim..."

  xhost +local:docker >/dev/null 2>&1 || true
  mkdir -p "${ISAAC_DATA_ROOT}/cache/main/ov" "${ISAAC_DATA_ROOT}/cache/main/warp" \
           "${ISAAC_DATA_ROOT}/cache/computecache" "${ISAAC_DATA_ROOT}/config" \
           "${ISAAC_DATA_ROOT}/data/documents" "${ISAAC_DATA_ROOT}/data/Kit" \
           "${ISAAC_DATA_ROOT}/logs"

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
  -v '${ISAAC_DATA_ROOT}/cache/main/ov:/home/ubuntu/.cache/ov:rw' \\
  -v '${ISAAC_DATA_ROOT}/cache/main/warp:/home/ubuntu/.cache/warp:rw' \\
  -v '${ISAAC_DATA_ROOT}/cache/computecache:/home/ubuntu/.nv/ComputeCache:rw' \\
  -v '${ISAAC_DATA_ROOT}/config:/home/ubuntu/.nvidia-omniverse/config:rw' \\
  -v '${ISAAC_DATA_ROOT}/data/documents:/home/ubuntu/Documents:rw' \\
  -v '${ISAAC_DATA_ROOT}/data/Kit:/home/ubuntu/.local/share/ov/data/Kit:rw' \\
  -v '${ISAAC_DATA_ROOT}/logs:/home/ubuntu/.nvidia-omniverse/logs:rw' \\
  -v '${REPO_ROOT}:/workspace:rw' \\
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

launch_raspi_radio_video_yolo_iperf() {
  log "Lanzando stack Raspi/AP13/STA12: cámara + MP4 + iperf + YOLO + throughput local..."

  open_terminal "T3 - Raspi 60GHz Video YOLO iperf" "
set -e
cd '${GO2_ROOT}'
IPERF_BITRATE='${IPERF_BITRATE}' \\
THROUGHPUT_SCALE_MAX='${THROUGHPUT_SCALE_MAX}' \\
THROUGHPUT_SMOOTH_SAMPLES='${THROUGHPUT_SMOOTH_SAMPLES}' \\
MP4_FILE='${MP4_FILE}' \\
RASPI_ORIGINAL_ENABLE_MP4='0' \\
SPARK_IP='${SPARK_IP}' \\
RASPI_HOST='${RASPI_HOST}' \\
RASPI_MGMT_HOST='${RASPI_MGMT_HOST}' \\
'${RASPI_STACK_SH}'
"
}

launch_zone_loop() {
  log "Lanzando zone loop patrol con AprilTag local Spark /dev/video2..."

  mkdir -p "${GO2_ROOT}/debug_logs"

  open_terminal "T8 - Zone Loop Patrol" "
set -e
cd '${ROS_WS}'
source /opt/ros/jazzy/setup.bash
source install/setup.bash 2>/dev/null || true

ZONE_LOG='${GO2_ROOT}/debug_logs/zone_loop_patrol_'\$(date +%Y%m%d_%H%M%S)'.log'
echo '[ZONE] log='\${ZONE_LOG}

python '${ZONE_LOOP_SCRIPT}' \\
  --ros-args \\
  -p mode:=auto \\
  -p camera_index:=${ZONE_LOOP_CAMERA_INDEX} \\
  -p apriltag_camera_device:=${APRILTAG_CAMERA_DEVICE} \\
  -p apriltag_id:=${ZONE_LOOP_APRILTAG_ID} \\
  -p cmd_topic:='${ZONE_LOOP_CMD_TOPIC}' \\
  -p input_file:='${PHASE_LAP_FILE}' \\
  -p repeat_auto:=true \\
  -p auto_align_enabled:=true \\
  -p direct_align_enabled:=true \\
  -p position_first:=true \\
  -p show_camera_window:=true \\
  -p enable_camera_stop:=true \\
  -p enable_mmwave_stop:=true \\
  -p enable_lidar_stop:=true \\
  -p tag_tol_x_m:=${ZONE_LOOP_TAG_TOL_X_M} \\
  -p tag_tol_y_m:=${ZONE_LOOP_TAG_TOL_Y_M} \\
  -p tag_tol_yaw_deg:=${ZONE_LOOP_TAG_TOL_YAW_DEG} \\
  -p person_confirm_sec:=0.5 \\
  -p clear_confirm_sec:=1.5 \\
  2>&1 | tee -a \"\${ZONE_LOG}\"

echo
echo '[ZONE] terminado. Pulsa ENTER para cerrar...'
read -r
"
}



launch_csi_stream_sta12() {
  log "Lanzando CSI stream desde STA .12 hacia live_csi_stream.txt..."

  mkdir -p "${GO2_ROOT}/csi_dog_dataset_20210421_181125/realtime_inputs"
  mkdir -p "${GO2_ROOT}/debug_logs"

  : > "${GO2_ROOT}/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt"

  open_terminal "R5 - CSI Stream STA12" "
set -e
CSI_OUT='${GO2_ROOT}/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt'
CSI_LOG='${GO2_ROOT}/debug_logs/csi_stream_sta12_'\$(date +%Y%m%d_%H%M%S)'.log'

echo '[CSI] destino='\"\${CSI_OUT}\" | tee -a \"\${CSI_LOG}\"
echo '[CSI] log='\"\${CSI_LOG}\" | tee -a \"\${CSI_LOG}\"
echo '[CSI] Spark -> Raspi WiFi -> STA .12' | tee -a \"\${CSI_LOG}\"

ssh -o ConnectTimeout=8 nextnet@'${RASPI_MGMT_HOST}' \\
  \"ssh -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa root@192.168.1.12 '/root/scripts_csi_dog/stream_csi_stdout_05s_ap13.sh'\" \\
  2> >(tee -a \"\${CSI_LOG}\" >&2) \\
  | tee -a \"\${CSI_OUT}\"

echo
echo '[CSI] terminado. Pulsa ENTER para cerrar...' | tee -a \"\${CSI_LOG}\"
read -r
"
}



launch_csi_predictor() {
  log "Lanzando predictor CSI live con modelo 20260526_all_1_2200..."

  open_terminal "T3 - CSI Predictor 0.5s" "
set -e
cd '${GO2_ROOT}'

mkdir -p '${GO2_ROOT}/debug_logs'
CSI_PRED_LOG='${GO2_ROOT}/debug_logs/csi_predictor_'\$(date +%Y%m%d_%H%M%S)'.log'

echo '[CSI-PRED] input=${CSI_STREAM_FILE}' | tee -a \"\${CSI_PRED_LOG}\"
echo '[CSI-PRED] model=${CSI_MODEL}' | tee -a \"\${CSI_PRED_LOG}\"
echo '[CSI-PRED] state=${CSI_STATE_JSON}' | tee -a \"\${CSI_PRED_LOG}\"
echo '[CSI-PRED] csv=${CSI_PREDICTIONS_CSV}' | tee -a \"\${CSI_PRED_LOG}\"
echo '[CSI-PRED] confirm=${CSI_CONFIRM_COUNT} window=${CSI_VOTE_WINDOW} threshold=${CSI_VOTE_THRESHOLD}' | tee -a \"\${CSI_PRED_LOG}\"

'${VENV_DIR}/bin/python' '${CSI_LIVE_PREDICTOR}' \\
  --input-file '${CSI_STREAM_FILE}' \\
  --model '${CSI_MODEL}' \\
  --state-json '${CSI_STATE_JSON}' \\
  --predictions-csv '${CSI_PREDICTIONS_CSV}' \\
  --confirm-count '${CSI_CONFIRM_COUNT}' \\
  --vote-window '${CSI_VOTE_WINDOW}' \\
  --vote-threshold '${CSI_VOTE_THRESHOLD}' \\
  --expected-tokens 72 \\
  --max-invalid-log-every 1 \\
  2>&1 | tee -a \"\${CSI_PRED_LOG}\"

echo
echo '[CSI-PRED] terminado. Pulsa ENTER para cerrar...'
read -r
"
}


launch_raspi_mp4_loop_clean_final() {
  log "Forzando MP4 golden_test en bucle real desde Raspi..."

  open_terminal "R6 - Raspi MP4 loop TX" "
set -e

RASPI_HOST='${RASPI_HOST}'
SPARK_IP='${SPARK_IP}'
MP4_FILE='${MP4_FILE}'
MP4_PORT='\${MP4_PORT:-6002}'
MP4_WIDTH='\${MP4_WIDTH:-1280}'
MP4_HEIGHT='\${MP4_HEIGHT:-720}'
MP4_FPS='\${MP4_FPS:-15}'
MP4_BITRATE_KBPS='\${MP4_BITRATE_KBPS:-20000}'
RTP_MTU='\${RTP_MTU:-1200}'

echo '[MP4-LOOP] Raspi='\"\${RASPI_HOST}\"' Spark='\"\${SPARK_IP}\"' file='\"\${MP4_FILE}\"' port='\"\${MP4_PORT}\"

ssh -o ConnectTimeout=8 nextnet@\"\${RASPI_HOST}\" \\
  \"pkill -9 -f 'gst-launch-1.0.*filesrc' 2>/dev/null || true\"

ssh -o ConnectTimeout=8 nextnet@\"\${RASPI_HOST}\" bash -s <<REMOTE_MP4
set -Eeuo pipefail

SPARK_IP='${SPARK_IP}'
MP4_FILE='${MP4_FILE}'
MP4_PORT='6002'
MP4_WIDTH='1280'
MP4_HEIGHT='720'
MP4_FPS='15'
MP4_BITRATE_KBPS='20000'
RTP_MTU='1200'

echo '[MP4-LOOP][RASPI] starting loop'
echo '[MP4-LOOP][RASPI] file='\${MP4_FILE}
echo '[MP4-LOOP][RASPI] dst='\${SPARK_IP}':'\${MP4_PORT}

while true; do
  gst-launch-1.0 -q \\
    filesrc location=\"\${MP4_FILE}\" ! \\
    qtdemux name=demux \\
    demux.video_0 ! queue ! decodebin ! \\
    videoconvert ! videoscale ! videorate ! \\
    video/x-raw,width=\${MP4_WIDTH},height=\${MP4_HEIGHT},framerate=\${MP4_FPS}/1 ! \\
    identity sync=true ! \\
    queue max-size-buffers=8 leaky=downstream ! \\
    x264enc tune=zerolatency pass=cbr bitrate=\${MP4_BITRATE_KBPS} speed-preset=ultrafast key-int-max=\${MP4_FPS} bframes=0 byte-stream=true sliced-threads=true vbv-buf-capacity=1000 option-string=\"nal-hrd=cbr:force-cfr=1\" ! \\
    h264parse config-interval=1 ! \\
    rtph264pay config-interval=1 pt=96 mtu=\${RTP_MTU} ! \\
    udpsink host=\"\${SPARK_IP}\" port=\"\${MP4_PORT}\" sync=false async=false

  rc=\$?
  echo '[MP4-LOOP][RASPI] gst ended rc='\${rc}', restarting in 0.5s'
  sleep 0.5
done
REMOTE_MP4

echo
echo '[MP4-LOOP] terminado. Pulsa ENTER para cerrar...'
read -r
"
}


launch_raspi_mp4_loop_clean_final() {
  log "Lanzando MP4 golden_test en bucle persistente desde Raspi hacia Spark:6002..."

  ssh -o ConnectTimeout=8 ${RASPI_USER}@"${RASPI_HOST}" bash -s <<REMOTE_MP4_LOOP
set -Eeuo pipefail

DEMO_DIR="${RASPI_RUNTIME_DIR}"
LOOP_SH="\${DEMO_DIR}/mp4_loop_tx_6002.sh"
LOG="\${DEMO_DIR}/logs/mp4_loop_tx_6002.log"
PIDFILE="\${DEMO_DIR}/logs/mp4_loop_tx_6002.pid"

mkdir -p "\${DEMO_DIR}/logs"

pkill -9 -f "gst-launch-1.0.*filesrc" 2>/dev/null || true
pkill -9 -f "mp4_loop_tx_6002.sh" 2>/dev/null || true
rm -f "\${PIDFILE}"

cat > "\${LOOP_SH}" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

SPARK_IP="\${SPARK_IP:-172.16.12.170}"
MP4_FILE="\${MP4_FILE:-${RASPI_RUNTIME_DIR}/golden_test.mp4}"
MP4_PORT="\${MP4_PORT:-6002}"

MP4_WIDTH="\${MP4_WIDTH:-1280}"
MP4_HEIGHT="\${MP4_HEIGHT:-720}"
MP4_FPS="\${MP4_FPS:-15}"
MP4_BITRATE_KBPS="\${MP4_BITRATE_KBPS:-20000}"
RTP_MTU="\${RTP_MTU:-1200}"

echo "[MP4-LOOP] start"
echo "[MP4-LOOP] file=\${MP4_FILE}"
echo "[MP4-LOOP] dst=\${SPARK_IP}:\${MP4_PORT}"

while true; do
  date '+[MP4-LOOP] %F %T launching gst'

  gst-launch-1.0 -q \
    filesrc location="\${MP4_FILE}" ! \
    qtdemux name=demux \
    demux.video_0 ! queue ! decodebin ! \
    videoconvert ! videoscale ! videorate ! \
    video/x-raw,width="\${MP4_WIDTH}",height="\${MP4_HEIGHT}",framerate="\${MP4_FPS}/1" ! \
    identity sync=true ! \
    queue max-size-buffers=8 leaky=downstream ! \
    x264enc tune=zerolatency pass=cbr bitrate="\${MP4_BITRATE_KBPS}" speed-preset=ultrafast key-int-max="\${MP4_FPS}" bframes=0 byte-stream=true sliced-threads=true vbv-buf-capacity=1000 option-string="nal-hrd=cbr:force-cfr=1" ! \
    h264parse config-interval=1 ! \
    rtph264pay config-interval=1 pt=96 mtu="\${RTP_MTU}" ! \
    udpsink host="\${SPARK_IP}" port="\${MP4_PORT}" sync=false async=false

  rc=\$?
  date "+[MP4-LOOP] %F %T gst ended rc=\${rc}; restarting in 0.5s"
  sleep 0.5
done
EOF

chmod +x "\${LOOP_SH}"

SPARK_IP="${SPARK_IP}" \
MP4_FILE="${MP4_FILE}" \
MP4_PORT="6002" \
nohup "\${LOOP_SH}" > "\${LOG}" 2>&1 &

echo \$! > "\${PIDFILE}"

sleep 1
echo "[MP4-LOOP] pid=\$(cat "\${PIDFILE}")"
tail -n 10 "\${LOG}" || true
REMOTE_MP4_LOOP
}


launch_raspi_mp4_loop_clean_final() {
  log "Lanzando MP4 en bucle automático GStreamer desde Raspi hacia Spark:6002..."

  ssh -o ConnectTimeout=8 ${RASPI_USER}@"${RASPI_HOST}" bash -s <<REMOTE_MP4_GST_LOOP
set -Eeuo pipefail

DEMO_DIR="${RASPI_RUNTIME_DIR}"
LOOP_SH="\${DEMO_DIR}/mp4_loop_tx_6002.sh"
LOG="\${DEMO_DIR}/logs/mp4_loop_tx_6002.log"
PIDFILE="\${DEMO_DIR}/logs/mp4_loop_tx_6002.pid"

mkdir -p "\${DEMO_DIR}/logs"

echo "[MP4-LOOP-AUTO] limpiando MP4 TX anterior..."
pkill -9 -f "mp4_loop_tx_6002.sh" 2>/dev/null || true
pkill -9 -f "gst-launch-1.0.*filesrc" 2>/dev/null || true
rm -f "\${PIDFILE}"

if [ ! -x "\${LOOP_SH}" ]; then
  echo "[MP4-LOOP-AUTO][ERROR] No existe o no es ejecutable: \${LOOP_SH}"
  exit 1
fi

SPARK_IP="${SPARK_IP}" \
MP4_FILE="${MP4_FILE}" \
MP4_PORT="6002" \
MP4_WIDTH="1280" \
MP4_HEIGHT="720" \
MP4_FPS="15" \
MP4_BITRATE_KBPS="20000" \
nohup "\${LOOP_SH}" > "\${LOG}" 2>&1 &

echo \$! > "\${PIDFILE}"

sleep 1

echo "[MP4-LOOP-AUTO] pid=\$(cat "\${PIDFILE}")"
ps -fp "\$(cat "\${PIDFILE}")" || true
echo "[MP4-LOOP-AUTO] log:"
tail -n 20 "\${LOG}" || true
REMOTE_MP4_GST_LOOP
}


launch_raspi_mp4_loop_clean_final() {
  log "Lanzando MP4 golden_test en bucle en ventana SSH viva..."

  open_terminal "R6 - Raspi MP4 Loop TX" "
set -e

echo '[MP4-LOOP-WINDOW] Conectando a Raspi ${RASPI_HOST}...'
ssh -o ConnectTimeout=8 nextnet@'${RASPI_HOST}' '
pkill -9 -f \"gst-launch-1.0.*filesrc\" 2>/dev/null || true
pkill -9 -f \"mp4_loop_tx_6002.sh\" 2>/dev/null || true

SPARK_IP=\"${SPARK_IP}\" \\
MP4_FILE=\"${MP4_FILE}\" \\
MP4_PORT=\"6002\" \\
MP4_WIDTH=\"1280\" \\
MP4_HEIGHT=\"720\" \\
MP4_FPS=\"15\" \\
MP4_BITRATE_KBPS=\"20000\" \\
${RASPI_RUNTIME_DIR}/mp4_loop_tx_6002.sh
'
"
}


launch_raspi_mp4_loop_clean_final() {
  log "Lanzando MP4 golden_test en bucle con launcher limpio R6..."

  RASPI_HOST="${RASPI_HOST}" \
  SPARK_IP="${SPARK_IP}" \
  MP4_FILE="${MP4_FILE}" \
  MP4_PORT="6002" \
  "${GO2_ROOT}/launch_r6_mp4_clean.sh"
}


launch_mp4_rx_simple_final() {
  log "Lanzando RX MP4 simple en Spark..."

  open_terminal "R2 - Raspi MP4 RX Simple" "
set -e
cd '${GO2_ROOT}'

gst-launch-1.0 -v \\
  udpsrc port=6002 \\
  caps=\"application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000\" ! \\
  rtpjitterbuffer latency=300 drop-on-latency=false ! \\
  rtph264depay ! \\
  h264parse ! \\
  avdec_h264 ! \\
  videoconvert ! \\
  autovideosink sync=false
"
}



launch_raspi_mp4_loop_clean_final() {
  log "Lanzando MP4 golden_test en bucle con R6 no bloqueante..."

  open_terminal "R6 - Raspi MP4 TX Simple" "
set +e
cd '${GO2_ROOT}'

RASPI_HOST='${RASPI_HOST}' \
RASPI_MGMT_HOST='${RASPI_MGMT_HOST}' \
SPARK_IP='${SPARK_IP}' \
MP4_FILE='${MP4_FILE}' \
MP4_PORT='6002' \
'${GO2_ROOT}/launch_r6_mp4_clean.sh'

rc=\$?
echo
echo '[R6] proceso terminado o falló con rc='\${rc}
echo '[R6] Pulsa ENTER para cerrar esta ventana...'
read -r
" || true

  return 0
}


main() {
  log "Iniciando FULL DEMO AP13/STA12 con stack Raspi sin setup de antenas."

  cleanup_stale_local_processes
  cleanup_raspi_processes
  preflight
  configure_final_local_route
  check_final_link_ready

  launch_isaac
  sleep 4

  launch_go2_slam_live_visual
  sleep 8

  launch_raspi_radio_video_yolo_iperf
  sleep 2
  launch_mp4_rx_simple_final
  sleep 5
  launch_raspi_mp4_loop_clean_final
  sleep 20
  launch_csi_stream_sta12

  sleep 2
  launch_csi_predictor
  sleep 2
  launch_zone_loop

  log "Todo lanzado."
  log "Ventanas esperadas:"
  log "  T1 Isaac Sim"
  log "  T2 Go2 SLAM Live Visual"
  log "  T3 Raspi 60GHz + subventanas R1/R2/R3/R4"
  log "  T8 Zone Loop Patrol"
  log ""
  log "Stack radio: Raspi -> AP .13 -> STA .12 -> Spark."
  log "Setup de antenas NO incluido en este run."
  log "Para parar stack Raspi:"
  log "  ${RASPI_STOP_SH}"
}



main "$@"
