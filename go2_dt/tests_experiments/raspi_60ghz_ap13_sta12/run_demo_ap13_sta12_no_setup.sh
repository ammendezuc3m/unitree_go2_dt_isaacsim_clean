#!/usr/bin/env bash

# DEBUG_ERR_TRAP_AP13_STA12
trap 'rc=$?; echo; echo "[raspi-stack][ERROR] fallo en línea ${LINENO}, rc=${rc}"; echo "[raspi-stack][ERROR] comando: ${BASH_COMMAND}"; echo "[raspi-stack][ERROR] log visible: tests_experiments/tmp_outputs/raspi_60ghz_ap13_sta12/"; exit ${rc}' ERR

set -Eeuo pipefail

# ============================================================
# RASPI 60GHz STACK - AP13 / STA12 - NO SETUP
#
# Este script NO configura antenas.
# Asume que ya se ha hecho:
#   setup_ap13_sta12_link.sh
#
# Topología final:
#   Raspi 172.16.13.100 -> AP .13 -> 60GHz -> STA .12 -> Spark 172.16.12.170
#
# Lanza:
#   R1 iperf server Spark
#   R2 MP4 RX Spark
#   R3 Camera RTP + YOLO RX Spark
#   R4 Throughput local azul
#   Raspi sender: camera + MP4 + iperf
# ============================================================

GO2_ROOT="${GO2_ROOT:-/home/nextnet/AlbertoDir/go2_dt}"
YOLO_PYTHON="${GO2_ROOT}/venvs/webcam_yolo_env/bin/python"

SPARK_IF="${SPARK_IF:-enP7s7}"
SPARK_IP="${SPARK_IP:-172.16.12.170}"
RASPI_HOST="${RASPI_HOST:-172.16.13.100}"
RASPI_MGMT_HOST="${RASPI_MGMT_HOST:-192.168.1.100}"

IPERF_PORT="${IPERF_PORT:-5201}"
IPERF_MODE="${IPERF_MODE:-tcp}"
IPERF_BITRATE="${IPERF_BITRATE:-400M}"
MP4_FILE="${MP4_FILE:-/home/nextnet/raspi_60ghz_demo/golden_test.mp4}"

CAMERA_PORT="${CAMERA_PORT:-6000}"
MP4_PORT="${MP4_PORT:-6002}"
MP4_RX_RESTART_SEC="${MP4_RX_RESTART_SEC:-220}"
RASPI_ORIGINAL_ENABLE_MP4="${RASPI_ORIGINAL_ENABLE_MP4:-1}"

ENABLE_CAMERA="${ENABLE_CAMERA:-1}"
ENABLE_MP4="${ENABLE_MP4:-0}"
ENABLE_IPERF="${ENABLE_IPERF:-1}"
ENABLE_YOLO="${ENABLE_YOLO:-1}"
ENABLE_THROUGHPUT="${ENABLE_THROUGHPUT:-1}"
ENABLE_CSI="${ENABLE_CSI:-1}"
ENABLE_CSI_PREDICTOR="${ENABLE_CSI_PREDICTOR:-1}"
STA_HOST="${STA_HOST:-172.16.12.1}"

CSI_STREAM_FILE="${CSI_STREAM_FILE:-${GO2_ROOT}/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt}"
CSI_STATE_JSON="${CSI_STATE_JSON:-${GO2_ROOT}/csi_dog_dataset_20210421_181125/analysis_outputs/live_prediction_state.json}"
CSI_PREDICTIONS_CSV="${CSI_PREDICTIONS_CSV:-${GO2_ROOT}/csi_dog_dataset_20210421_181125/analysis_outputs/live_predictions_external_stream.csv}"
CSI_MODEL="${CSI_MODEL:-${GO2_ROOT}/csi_dog_dataset_20210421_181125/analysis_outputs/csi_empty_person_model_20260526_all_1_2200.joblib}"
CSI_STREAM_SH="${CSI_STREAM_SH:-${GO2_ROOT}/tests_experiments/raspi_60ghz_ap13_sta12/csi_sta12_stream_to_file.sh}"
CSI_PREDICTOR_SH="${CSI_PREDICTOR_SH:-${GO2_ROOT}/tests_experiments/raspi_60ghz_ap13_sta12/run_csi_predictor_ap13_sta12.sh}"

THROUGHPUT_SCALE_MAX="${THROUGHPUT_SCALE_MAX:-500}"
THROUGHPUT_SMOOTH_SAMPLES="${THROUGHPUT_SMOOTH_SAMPLES:-30}"

YOLO_TSIZE="192"
YOLO_PROCESS_FPS="10"
YOLO_CONF="${YOLO_CONF:-0.25}"
YOLO_NMS="${YOLO_NMS:-0.45}"

YOLO_JSON_OUT="${YOLO_JSON_OUT:-${GO2_ROOT}/camera_yolo/outputs/live_camera_state.json}"
YOLO_EXP_FILE="${YOLO_EXP_FILE:-${GO2_ROOT}/camera_yolo/YOLOX/exps/default/yolox_s.py}"
YOLO_CKPT="${YOLO_CKPT:-${GO2_ROOT}/camera_yolo/Weights/yolox_s.pth}"

THROUGHPUT_LOCAL_PY="${GO2_ROOT}/tests_experiments/raspi_60ghz_ap13_sta12/throughput_local_rx_tk_smooth.py"
YOLO_RX_PY="${GO2_ROOT}/tests_experiments/raspi_60ghz/rx_camera_rtp_yolox_realtime_pipe.py"
YOLO_PY="/home/nextnet/AlbertoDir/go2_dt/venvs/webcam_yolo_env/bin/python"

LOG_DIR="${GO2_ROOT}/tests_experiments/tmp_outputs/raspi_60ghz_ap13_sta12"
mkdir -p "${LOG_DIR}"

log() { echo -e "\033[1;32m[raspi-stack]\033[0m $*"; }
warn() { echo -e "\033[1;33m[raspi-stack][WARN]\033[0m $*" >&2; }
err() { echo -e "\033[1;31m[raspi-stack][ERROR]\033[0m $*" >&2; }

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

check_file() {
  local f="$1"
  if [[ ! -e "$f" ]]; then
    err "No existe: $f"
    exit 1
  fi
}

cleanup_local() {
  log "Limpiando procesos locales del stack Raspi..."

  pkill -TERM -f "iperf3 -s" 2>/dev/null || true
  pkill -TERM -f "gst-launch-1.0.*udpsrc.*${MP4_PORT}" 2>/dev/null || true
  pkill -TERM -f "rx_camera_rtp_yolox_realtime_pipe.py" 2>/dev/null || true
  pkill -TERM -f "throughput_local_rx_tk_smooth.py" 2>/dev/null || true
  pkill -TERM -f "csi_sta12_stream_to_file.sh" 2>/dev/null || true
  pkill -TERM -f "run_csi_predictor_ap13_sta12.sh" 2>/dev/null || true
  pkill -TERM -f "csi_live_predictor_from_stream.py" 2>/dev/null || true

  sleep 1

  pkill -KILL -f "iperf3 -s" 2>/dev/null || true
  pkill -KILL -f "gst-launch-1.0.*udpsrc.*${MP4_PORT}" 2>/dev/null || true
  pkill -KILL -f "rx_camera_rtp_yolox_realtime_pipe.py" 2>/dev/null || true
  pkill -KILL -f "throughput_local_rx_tk_smooth.py" 2>/dev/null || true
  pkill -KILL -f "csi_sta12_stream_to_file.sh" 2>/dev/null || true
  pkill -KILL -f "run_csi_predictor_ap13_sta12.sh" 2>/dev/null || true
  pkill -KILL -f "csi_live_predictor_from_stream.py" 2>/dev/null || true

  fuser -k "${IPERF_PORT}/tcp" 2>/dev/null || true
  fuser -k "${IPERF_PORT}/udp" 2>/dev/null || true
  fuser -k "${CAMERA_PORT}/udp" 2>/dev/null || true
  fuser -k "${MP4_PORT}/udp" 2>/dev/null || true
}

cleanup_raspi() {
  log "Limpiando procesos antiguos en Raspi..."

  ssh -o ConnectTimeout=5 nextnet@"${RASPI_HOST}" '
for pid in $(ps -eo pid,args | awk "/iperf3|gst-launch-1.0|v4l2src|x264enc|raspi_60ghz_sender|while true|mp4-loop|iperf-loop/ && !/awk/ {print \$1}"); do
  kill -9 "$pid" 2>/dev/null || true
done
killall -9 iperf3 2>/dev/null || true
killall -9 gst-launch-1.0 2>/dev/null || true
' || {
    warn "No pude limpiar por demo IP ${RASPI_HOST}; pruebo gestión ${RASPI_MGMT_HOST}"
    ssh -o ConnectTimeout=5 nextnet@"${RASPI_MGMT_HOST}" '
for pid in $(ps -eo pid,args | awk "/iperf3|gst-launch-1.0|v4l2src|x264enc|raspi_60ghz_sender|while true|mp4-loop|iperf-loop/ && !/awk/ {print \$1}"); do
  kill -9 "$pid" 2>/dev/null || true
done
killall -9 iperf3 2>/dev/null || true
killall -9 gst-launch-1.0 2>/dev/null || true
' || true
  }
}

ensure_spark_route() {
  log "Asegurando ruta Spark -> Raspi por STA .12..."
  echo "[raspi-stack] Comprobando IP/ruta Spark sin sudo..."
  ip -br a show "${SPARK_IF}" || true
  ip route get "${RASPI_HOST}" || true


  ip route get "${RASPI_HOST}" || true
}

preflight() {
  log "Preflight stack Raspi..."

  check_file "${YOLO_RX_PY}"
  check_file "${THROUGHPUT_LOCAL_PY}"
  check_file "${CSI_STREAM_SH}"
  check_file "${CSI_PREDICTOR_SH}"
  check_file "${CSI_MODEL}"
  check_file "${YOLO_EXP_FILE}"
  check_file "${YOLO_CKPT}"

  python3 -m py_compile "${YOLO_RX_PY}"
  python3 -m py_compile "${THROUGHPUT_LOCAL_PY}"
  bash -n "${CSI_STREAM_SH}"
  bash -n "${CSI_PREDICTOR_SH}"

  ping -c 5 -W 2 "${RASPI_HOST}"

  ssh -o ConnectTimeout=5 nextnet@"${RASPI_HOST}" "
echo SSH_OK_RASPI
ip route get '${SPARK_IP}'
test -e /dev/video4 && echo '/dev/video4 OK' || echo '/dev/video4 MISSING'
test -x /home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh && echo 'sender OK' || echo 'sender MISSING'
"
}

launch_iperf_server() {
  log "Lanzando iperf3 server en Spark..."

  open_terminal "R1 - iperf server Spark" "
set -e
iperf3 -s -B '${SPARK_IP}' -p '${IPERF_PORT}' -i 1
"
}

launch_mp4_rx() {
  if [[ "${ENABLE_MP4}" != "1" ]]; then
    log "MP4 RX desactivado."
    return
  fi

  log "Lanzando receptor MP4 estable en Spark..."

  open_terminal "R2 - Raspi MP4 RX" "
set -e
cd '${GO2_ROOT}'
SPARK_IP='${SPARK_IP}' \
MP4_PORT='${MP4_PORT}' \
'${GO2_ROOT}/tests_experiments/raspi_60ghz_ap13_sta12/mp4_rx_stable_6002.sh'
"
}



launch_yolo_rx() {
  if [[ "${ENABLE_CAMERA}" != "1" || "${ENABLE_YOLO}" != "1" ]]; then
    log "YOLO RX desactivado."
    return
  fi

  log "Lanzando cámara Raspi + YOLO RX en Spark..."

  mkdir -p "$(dirname "${YOLO_JSON_OUT}")"
  : > "${LOG_DIR}/rx_yolo.log"

  open_terminal "R3 - Raspi Camera YOLO RX" "
set -e
cd '${GO2_ROOT}'
'${YOLO_PY}' '${YOLO_RX_PY}' \
  --port '${CAMERA_PORT}' \
  --latency-ms '80' \
  --width 424 \
  --height 240 \
  --fps 15 \
  --json-out '${YOLO_JSON_OUT}' \
  --yolo-exp-file '${YOLO_EXP_FILE}' \
  --yolo-ckpt '${YOLO_CKPT}' \
  --yolo-device 'gpu' \
  --yolo-conf '${YOLO_CONF}' \
  --yolo-nms '${YOLO_NMS}' \
  --yolo-tsize '${YOLO_TSIZE}' \
  --process-fps '${YOLO_PROCESS_FPS}' \
  --window-name 'raspi-rx-yolo' \
  --yolo-fp16 2>&1 | tee -a '${LOG_DIR}/rx_yolo.log'
"
}

launch_throughput() {
  if [[ "${ENABLE_THROUGHPUT}" != "1" ]]; then
    log "Throughput desactivado."
    return
  fi

  log "Lanzando throughput local azul..."

  open_terminal "R4 - 60GHz Local RX Throughput" "
set -e
cd '${GO2_ROOT}'
python3 '${THROUGHPUT_LOCAL_PY}' \
  --iface '${SPARK_IF}' \
  --scale-max '${THROUGHPUT_SCALE_MAX}' \
  --window 240 \
  --refresh 1.0 \
  --smooth-samples 3
"
}


launch_csi_stream() {
  if [[ "${ENABLE_CSI}" != "1" ]]; then
    log "CSI stream desactivado."
    return
  fi

  log "Lanzando CSI STA12 -> live_csi_stream.txt cada ~0.5s..."

  open_terminal "R5 - CSI STA12 Stream" "
set -e
cd '${GO2_ROOT}'
STA_HOST='${STA_HOST}' \
CSI_STREAM_FILE='${CSI_STREAM_FILE}' \
CSI_MODEL="${CSI_MODEL:-${GO2_ROOT}/csi_dog_dataset_20210421_181125/analysis_outputs/csi_empty_person_model_20260526_all_1_2200.joblib}"
CSI_STATE_JSON='${CSI_STATE_JSON}' \
CSI_PREDICTIONS_CSV='${CSI_PREDICTIONS_CSV}' \
'${CSI_STREAM_SH}'
"
}

launch_csi_predictor() {
  if [[ "${ENABLE_CSI}" != "1" || "${ENABLE_CSI_PREDICTOR}" != "1" ]]; then
    log "CSI predictor desactivado."
    return
  fi

  log "Lanzando CSI predictor..."

  open_terminal "R6 - CSI Live Predictor" "
set -e
cd '${GO2_ROOT}'
CSI_STREAM_FILE='${CSI_STREAM_FILE}' \
CSI_MODEL="${CSI_MODEL:-${GO2_ROOT}/csi_dog_dataset_20210421_181125/analysis_outputs/csi_empty_person_model_20260526_all_1_2200.joblib}"
CSI_STATE_JSON='${CSI_STATE_JSON}' \
CSI_PREDICTIONS_CSV='${CSI_PREDICTIONS_CSV}' \
'${CSI_PREDICTOR_SH}'
"
}

launch_raspi_sender() {
  log "Ordenando a Raspi iniciar camera + MP4 + iperf..."

  ssh -o ConnectTimeout=5 nextnet@"${RASPI_HOST}" "
SPARK_IP='${SPARK_IP}' \
ENABLE_CAMERA='${ENABLE_CAMERA}' \
ENABLE_IPERF='${ENABLE_IPERF}' \
IPERF_BITRATE='${IPERF_BITRATE}' \
IPERF_MODE='${IPERF_MODE:-tcp}' \
ENABLE_MP4='0' \
ENABLE_MP4="${ENABLE_MP4:-0}"
IPERF_MODE='tcp' \
IPERF_PORT='${IPERF_PORT}' \
CAMERA_PORT='${CAMERA_PORT}' \
MP4_PORT='${MP4_PORT}' \
/home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
"
}

main() {
  log "RUN stack Raspi AP13/STA12 sin setup de antenas."
  log "Raspi=${RASPI_HOST}, Spark=${SPARK_IP}, iperf=${IPERF_BITRATE}"

  cleanup_local
  cleanup_raspi
  ensure_spark_route
  preflight

  launch_iperf_server
  sleep 2

  launch_mp4_rx
  sleep 1

  launch_yolo_rx
  sleep 1

  launch_throughput
  sleep 1

  launch_csi_stream
  sleep 1

  launch_csi_predictor
  sleep 1

  launch_raspi_sender

  log "Stack Raspi lanzado."
  log "Ventanas esperadas: R1 iperf, R2 MP4, R3 YOLO, R4 Throughput, R5 CSI, R6 CSI Predictor."
}

main "$@"
