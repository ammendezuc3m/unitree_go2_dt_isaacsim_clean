#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GO2_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

RASPI_USER="${RASPI_USER:-system}"
RASPI_HOST="${RASPI_HOST:-172.16.12.100}"

SPARK_DEMO_IP="${SPARK_DEMO_IP:-172.16.13.170}"
SPARK_IFACE="${SPARK_IFACE:-enP7s7}"

STA_HOST="${STA_HOST:-192.168.1.13}"
STA_USER="${STA_USER:-root}"

CAMERA_PORT="${CAMERA_PORT:-6000}"
MP4_PORT="${MP4_PORT:-6002}"
IPERF_PORT="${IPERF_PORT:-5201}"

CAMERA_BITRATE_KBPS="${CAMERA_BITRATE_KBPS:-2000}"
MP4_BITRATE_KBPS="${MP4_BITRATE_KBPS:-20000}"
ENABLE_MP4="${ENABLE_MP4:-1}"

IPERF_MODE="${IPERF_MODE:-tcp}"
IPERF_BITRATE="${IPERF_BITRATE:-100M}"
IPERF_UDP_LENGTH="${IPERF_UDP_LENGTH:-1470}"

THROUGHPUT_SCALE_MAX="${THROUGHPUT_SCALE_MAX:-160}"
THROUGHPUT_SAMPLES="${THROUGHPUT_SAMPLES:-240}"
THROUGHPUT_INTERVAL="${THROUGHPUT_INTERVAL:-1}"
THROUGHPUT_SMOOTH_SAMPLES="${THROUGHPUT_SMOOTH_SAMPLES:-5}"

CSI_ENABLE="${CSI_ENABLE:-1}"
CSI_PERIOD_S="${CSI_PERIOD_S:-0.5}"
CSI_FILE="${CSI_FILE:-${GO2_ROOT}/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt}"

# Nuevo AP = .12, MAC wlan0 = 48:8f:5a:df:01:4e
CSI_AP_MAC_HEX="${CSI_AP_MAC_HEX:-\\x48\\x8f\\x5a\\xdf\\x01\\x4e}"

SSH_ANT_OPTS=(
  -o HostKeyAlgorithms=+ssh-rsa
  -o PubkeyAcceptedAlgorithms=+ssh-rsa
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=5
)

log() {
  echo -e "\033[1;32m[raspi-run]\033[0m $*"
}

warn() {
  echo -e "\033[1;33m[raspi-run][WARN]\033[0m $*" >&2
}

open_terminal() {
  local title="$1"
  local cmd="$2"

  if command -v gnome-terminal >/dev/null 2>&1; then
    gnome-terminal --title="$title" -- bash -lc "$cmd; echo; echo '[window] terminado. Ctrl+C/cierra para salir.'; exec bash"
  elif command -v xterm >/dev/null 2>&1; then
    xterm -T "$title" -e "bash -lc '$cmd; exec bash'" &
  else
    warn "No hay terminal gráfica. Ejecutando en background: $title"
    bash -lc "$cmd" &
  fi
}

cleanup_local() {
  log "Limpiando procesos locales previos..."

  pkill -f "rx_camera_rtp_yolox.py" 2>/dev/null || true
  pkill -f "throughput_live_tk.py" 2>/dev/null || true
  pkill -f "csi_live_predictor_from_stream.py" 2>/dev/null || true
  pkill -f "CSI pull STA13" 2>/dev/null || true
  pkill -f "gst-launch-1.0.*udpsrc.*${MP4_PORT}" 2>/dev/null || true
  pkill -f "iperf3 -s.*${IPERF_PORT}" 2>/dev/null || true

  sudo fuser -k "${CAMERA_PORT}/udp" 2>/dev/null || true
  sudo fuser -k "${MP4_PORT}/udp" 2>/dev/null || true
  sudo fuser -k "${IPERF_PORT}/tcp" 2>/dev/null || true
  sudo fuser -k "${IPERF_PORT}/udp" 2>/dev/null || true
}

ensure_routes() {
  log "Asegurando IP/ruta demo en Spark..."

  sudo ip addr add "${SPARK_DEMO_IP}/24" dev "${SPARK_IFACE}" 2>/dev/null || true
  sudo ip route replace 172.16.12.0/24 via 172.16.13.1 dev "${SPARK_IFACE}" src "${SPARK_DEMO_IP}"
  sudo ip route flush cache

  echo
  echo "=== route Spark -> Raspi demo ==="
  ip route get "${RASPI_HOST}" || true
}

check_connectivity() {
  log "Comprobando conectividad Raspi por ruta 60GHz..."

  ping -c 3 -W 2 "${RASPI_HOST}"

  log "Comprobando SSH Raspi..."
  ssh -o StrictHostKeyChecking=accept-new "${RASPI_USER}@${RASPI_HOST}" 'echo SSH_OK_RASPI_60GHZ; hostname'

  log "Comprobando SSH STA .13 para CSI..."
  ssh "${SSH_ANT_OPTS[@]}" "${STA_USER}@${STA_HOST}" 'echo SSH_OK_STA13; iw dev wlan0 link || true'
}

start_iperf_server() {
  log "Lanzando iperf3 server en Spark..."

  open_terminal "R1 - iperf3 server Spark" "
cd '${GO2_ROOT}'
iperf3 -s -B '${SPARK_DEMO_IP}' -p '${IPERF_PORT}' -i 1
"
}

start_camera_yolo_rx() {
  log "Lanzando RX cámara Raspi + YOLO en Spark..."

  open_terminal "R2 - Raspi camera RX + YOLO" "
cd '${GO2_ROOT}'
mkdir -p '${GO2_ROOT}/tests_experiments/tmp_outputs/raspi_60ghz'
: > '${GO2_ROOT}/tests_experiments/tmp_outputs/raspi_60ghz/rx_yolo.log'
python3 tests_experiments/raspi_60ghz/rx_camera_rtp_yolox.py \
  --port '${CAMERA_PORT}' \
  --latency-ms 200 \
  --json-out '${GO2_ROOT}/camera_yolo/outputs/live_camera_state.json' \
  --yolo-exp-file '${GO2_ROOT}/camera_yolo/YOLOX/exps/default/yolox_s.py' \
  --yolo-ckpt '${GO2_ROOT}/camera_yolo/Weights/yolox_s.pth' \
  --yolo-device gpu \
  --yolo-conf 0.25 \
  --yolo-nms 0.45 \
  --yolo-tsize 256 \
  --yolo-fp16 2>&1 | tee -a '${GO2_ROOT}/tests_experiments/tmp_outputs/raspi_60ghz/rx_yolo.log'
"
}

start_mp4_rx() {
  if [[ "${ENABLE_MP4}" != "1" ]]; then
    log "MP4 RX desactivado."
    return 0
  fi

  log "Lanzando RX MP4 Raspi en Spark..."

  open_terminal "R3 - Raspi MP4 RX" "
gst-launch-1.0 -v \
  udpsrc port='${MP4_PORT}' caps='application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000' ! \
  rtpjitterbuffer latency=500 drop-on-latency=false ! \
  rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! \
  fpsdisplaysink video-sink=autovideosink sync=false text-overlay=true
"
}

start_throughput_plot() {
  log "Lanzando gráfica throughput..."

  open_terminal "R4 - 60GHz Live Throughput" "
cd '${GO2_ROOT}'
python3 throughput_live_tk.py \
  --local-iface '${SPARK_IFACE}' \
  --sta-host '${STA_HOST}' \
  --scale-max '${THROUGHPUT_SCALE_MAX}' \
  --samples '${THROUGHPUT_SAMPLES}' \
  --interval '${THROUGHPUT_INTERVAL}' \
  --smooth-samples '${THROUGHPUT_SMOOTH_SAMPLES}'
"
}

start_csi() {
  if [[ "${CSI_ENABLE}" != "1" ]]; then
    log "CSI desactivado."
    return 0
  fi

  log "Preparando CSI desde STA .13 hacia AP .12..."
  mkdir -p "$(dirname "${CSI_FILE}")"
  : > "${CSI_FILE}"

  # Limpiamos dmesg en la STA para evitar muestras antiguas.
  ssh "${SSH_ANT_OPTS[@]}" "${STA_USER}@${STA_HOST}" 'dmesg -c >/dev/null 2>&1 || true' || true

  open_terminal "R5 - CSI pull STA13" "
cd '${GO2_ROOT}'
echo '[CSI] Pull desde STA .13 hacia AP .12'
echo '[CSI] Output: ${CSI_FILE}'
echo '[CSI] Period: ${CSI_PERIOD_S}s'
while true; do
  LINE=\"\$(ssh -oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedAlgorithms=+ssh-rsa -oStrictHostKeyChecking=accept-new ${STA_USER}@${STA_HOST} \"
    printf '%b' '${CSI_AP_MAC_HEX}' | iw dev wlan0 vendor recv 0x001374 0x93 - >/dev/null 2>&1
    dmesg -c 2>/dev/null | grep '\\[AOA\\] Measurement' | tail -n 1
  \" 2>/dev/null || true)\"

  if [[ -n \"\${LINE}\" ]]; then
    echo \"\${LINE}\" >> '${CSI_FILE}'
    echo \"\${LINE}\"
  else
    echo '[CSI][WARN] sin medida'
  fi

  sleep '${CSI_PERIOD_S}'
done
"

  sleep 1

  log "Lanzando predictor CSI local..."
  open_terminal "R6 - CSI Predictor" "
cd '${GO2_ROOT}'
if python3 csi_live_predictor_from_stream.py --help 2>&1 | grep -q -- '--input'; then
  python3 csi_live_predictor_from_stream.py --input '${CSI_FILE}'
else
  python3 csi_live_predictor_from_stream.py
fi
"
}

start_raspi_sender() {
  log "Ordenando a Raspi iniciar TX: cámara + MP4 + iperf ${IPERF_BITRATE}..."

  ssh -o StrictHostKeyChecking=accept-new "${RASPI_USER}@${RASPI_HOST}" "
SPARK_IP='${SPARK_DEMO_IP}' \
CAMERA_DEVICE='/dev/video4' \
CAMERA_WIDTH='424' \
CAMERA_HEIGHT='240' \
CAMERA_FPS='15' \
CAMERA_BITRATE_KBPS='${CAMERA_BITRATE_KBPS}' \
CAMERA_PORT='${CAMERA_PORT}' \
ENABLE_MP4='${ENABLE_MP4}' \
MP4_BITRATE_KBPS='${MP4_BITRATE_KBPS}' \
MP4_PORT='${MP4_PORT}' \
IPERF_MODE='${IPERF_MODE}' \
IPERF_BITRATE='${IPERF_BITRATE}' \
IPERF_UDP_LENGTH='${IPERF_UDP_LENGTH}' \
IPERF_PORT='${IPERF_PORT}' \
/home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
"
}

main() {
  log "RUN Raspi -> AP .12 -> 60GHz -> STA .13 -> Spark"
  log "Spark: RX vídeo + YOLO + CSI + throughput + iperf server"
  log "Raspi: cámara + MP4 + iperf ${IPERF_BITRATE}"

  cleanup_local
  ensure_routes
  check_connectivity

  start_iperf_server
  start_camera_yolo_rx
  start_mp4_rx
  start_throughput_plot
  start_csi

  sleep 3
  start_raspi_sender

  log "Todo lanzado."
  log "Raspi logs:"
  log "  ssh ${RASPI_USER}@${RASPI_HOST} 'tail -f /home/system/raspi_60ghz_demo/logs/*.log'"
}

main "$@"
