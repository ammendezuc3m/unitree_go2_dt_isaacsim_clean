#!/usr/bin/env bash
set -Eeuo pipefail

GO2_ROOT="${GO2_ROOT:-/home/nextnet/AlbertoDir/go2_dt}"
PYTHON_BIN="${PYTHON_BIN:-${GO2_ROOT}/.venv/bin/python}"

RASPI_HOST="${RASPI_HOST:-172.16.12.100}"
RASPI_USER="${RASPI_USER:-system}"
SPARK_IP="${SPARK_IP:-172.16.13.170}"
STA_MGMT_HOST="${STA_MGMT_HOST:-192.168.1.13}"

IPERF_PORT="${IPERF_PORT:-5201}"
IPERF_BITRATE="${IPERF_BITRATE:-100M}"
IPERF_MODE="${IPERF_MODE:-tcp}"

CAMERA_PORT="${CAMERA_PORT:-6000}"
MP4_PORT="${MP4_PORT:-6002}"

ENABLE_CAMERA="${ENABLE_CAMERA:-1}"
ENABLE_MP4="${ENABLE_MP4:-1}"
ENABLE_IPERF="${ENABLE_IPERF:-1}"
ENABLE_THROUGHPUT="${ENABLE_THROUGHPUT:-1}"
ENABLE_CSI="${ENABLE_CSI:-0}"

THROUGHPUT_SCALE_MAX="${THROUGHPUT_SCALE_MAX:-160}"
YOLO_TSIZE="${YOLO_TSIZE:-160}"
YOLO_PROCESS_FPS="${YOLO_PROCESS_FPS:-1}"

LOG_DIR="${GO2_ROOT}/tests_experiments/tmp_outputs/raspi_60ghz"
mkdir -p "${LOG_DIR}"

log() {
  echo "[raspi-pipeline] $*"
}

open_terminal() {
  local title="$1"
  local cmd="$2"
  gnome-terminal --title="$title" -- bash -lc "$cmd; echo; echo '[${title}] terminado. Pulsa Enter para cerrar.'; read -r" &
}

cd "${GO2_ROOT}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[raspi-pipeline][ERROR] Missing runtime Python: $PYTHON_BIN"
  echo "[raspi-pipeline][ERROR] Create it from requirements/requirements-runtime.txt first."
  exit 1
fi

log "RUN Raspi -> AP .12 -> 60GHz -> STA .13 -> Spark"
log "Raspi: camera=${ENABLE_CAMERA}, mp4=${ENABLE_MP4}, iperf=${ENABLE_IPERF}"
log "Spark: MP4 RX + YOLO pipe + throughput. CSI=${ENABLE_CSI}"

log "Limpiando restos previos..."
"${GO2_ROOT}/tests_experiments/raspi_60ghz/stop_raspi_60ghz_pipeline.sh" || true

log "Comprobando ruta Spark -> Raspi por 172.16..."
ping -c 3 -W 2 "${RASPI_HOST}"

log "Comprobando enlace radio STA .13 -> AP .12..."
ssh -oHostKeyAlgorithms=+ssh-rsa \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    -oConnectTimeout=5 \
    root@"${STA_MGMT_HOST}" '
iw dev wlan0 link
ping -I wlan0 -c 3 -W 2 10.10.10.1
'

log "Comprobando cámara RealSense en Raspi..."
ssh -o ConnectTimeout=5 "${RASPI_USER}@${RASPI_HOST}" '
v4l2-ctl --list-devices | grep -A8 -i "RealSense" || true
test -e /dev/video4
'

log "Lanzando iperf3 server en Spark..."
open_terminal "R1 - iperf server Spark" "
iperf3 -s -B '${SPARK_IP}' -p '${IPERF_PORT}' -i 1
"

sleep 1

if [[ "${ENABLE_MP4}" == "1" ]]; then
  log "Lanzando receptor MP4 en Spark..."
  open_terminal "R2 - Raspi MP4 RX" "
gst-launch-1.0 -v \
  udpsrc port='${MP4_PORT}' caps='application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000' ! \
  rtpjitterbuffer latency=500 drop-on-latency=false ! \
  rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! \
  fpsdisplaysink video-sink=autovideosink sync=false text-overlay=true
"
fi

if [[ "${ENABLE_CAMERA}" == "1" ]]; then
  log "Lanzando receptor YOLO pipe en Spark..."
  open_terminal "R3 - Raspi Camera YOLO RX" "
cd '${GO2_ROOT}'
"$PYTHON_BIN" tests_experiments/raspi_60ghz/rx_camera_rtp_yolox_pipe.py \
  --port '${CAMERA_PORT}' \
  --latency-ms 300 \
  --width 424 \
  --height 240 \
  --fps 15 \
  --json-out '${GO2_ROOT}/camera_yolo/outputs/live_camera_state.json' \
  --yolo-exp-file '${GO2_ROOT}/camera_yolo/YOLOX/exps/default/yolox_s.py' \
  --yolo-ckpt '${GO2_ROOT}/camera_yolo/Weights/yolox_s.pth' \
  --yolo-device cpu \
  --yolo-conf 0.25 \
  --yolo-nms 0.45 \
  --yolo-tsize '${YOLO_TSIZE}' \
  --process-fps '${YOLO_PROCESS_FPS}' \
  --window-name 'raspi-rx-yolo'
"
fi

if [[ "${ENABLE_THROUGHPUT}" == "1" ]]; then
  log "Lanzando gráfica throughput..."
  open_terminal "R4 - 60GHz Throughput" "
cd '${GO2_ROOT}'
python3 throughput_live_tk.py \
  --local-iface enP7s7 \
  --sta-host '${STA_MGMT_HOST}' \
  --scale-max '${THROUGHPUT_SCALE_MAX}' \
  --samples 240 \
  --interval 1
"
fi

sleep 3

log "Lanzando sender en Raspi: cámara + MP4 + iperf..."
ssh "${RASPI_USER}@${RASPI_HOST}" "
SPARK_IP='${SPARK_IP}' \
ENABLE_CAMERA='${ENABLE_CAMERA}' \
ENABLE_MP4='${ENABLE_MP4}' \
ENABLE_IPERF='${ENABLE_IPERF}' \
CAMERA_DEVICE='/dev/video4' \
CAMERA_WIDTH='424' \
CAMERA_HEIGHT='240' \
CAMERA_FPS='15' \
CAMERA_BITRATE_KBPS='2000' \
CAMERA_PORT='${CAMERA_PORT}' \
MP4_BITRATE_KBPS='20000' \
MP4_PORT='${MP4_PORT}' \
IPERF_MODE='${IPERF_MODE}' \
IPERF_BITRATE='${IPERF_BITRATE}' \
IPERF_PORT='${IPERF_PORT}' \
IPERF_DURATION='3600' \
/home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
"

log "Todo lanzado."
log "Logs Raspi:"
log "  ssh ${RASPI_USER}@${RASPI_HOST} 'tail -f /home/system/raspi_60ghz_demo/logs/*.log'"
log "Stop:"
log "  RASPI_HOST=${RASPI_HOST} ${GO2_ROOT}/tests_experiments/raspi_60ghz/stop_raspi_60ghz_pipeline.sh"
