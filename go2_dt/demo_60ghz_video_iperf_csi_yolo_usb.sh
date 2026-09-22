#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# DEMO TEST: MP4 TX 60GHz + CSI 0.5s + iperf3 paralelo
# Portable: paths are derived from this script location.
#
# Objetivo:
# - Separar calidad de vídeo/red de la carga de YOLO.
# - TX de vídeo desde MP4 en bucle a 720p.
# - YOLO sobre cámara local a baja resolución.
# - CSI externo desde STA cada 0.5s.
#
# Roles:
# - AP  : 192.168.1.13 / wlan0 10.10.10.1
# - STA : 192.168.1.12 / wlan0 10.10.10.2
# - PC  : 192.168.1.170 / enP7s7
# ============================================================

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GO2_ROOT="${SCRIPT_DIR}"
REPO_ROOT="$(cd "${GO2_ROOT}/.." && pwd)"
PROJECT_PYTHON="${REPO_ROOT}/.venv/bin/python"
ROS_WS="${GO2_ROOT}/ros2_ws"

PY_SCRIPT="${GO2_ROOT}/video_file_tx_rx_yolo_usb.py"
CSI_LIVE_PREDICTOR="${GO2_ROOT}/csi_live_predictor_from_stream.py"
CSI_MODEL="${GO2_ROOT}/csi_dog_dataset_20210421_181125/analysis_outputs/csi_empty_person_model_20260526_all_1_2200.joblib"

VIDEO_FILE="${GO2_ROOT}/media/golden_test.mp4"

LOCAL_IFACE="enP7s7"
LOCAL_VIDEO_IP="192.168.1.170/24"
LOCAL_VIDEO_IP_PLAIN="192.168.1.170"

AP_HOST="192.168.1.13"
STA_HOST="192.168.1.12"
AP_USER="root"
STA_USER="root"

AP_WLAN_IP="10.10.10.1/24"
AP_WLAN_IP_PLAIN="10.10.10.1"
STA_WLAN_IP="10.10.10.2/24"
STA_WLAN_IP_PLAIN="10.10.10.2"

AP_VIDEO_PORT="5000"
PC_RX_PORT="5002"

SSID_60G="TEST-LINK"
CHANNEL_60G="2"
FREQ_60G="60480"

# Vídeo MP4 TX
TX_WIDTH=1280
TX_HEIGHT=720
TX_FPS=15

# Vídeo objetivo: 20 Mbps.
BITRATE_KBPS=20000
BUFFER_MS=500
RTP_MTU=1200
VBV_MS=1000

# iperf3 UDP paralelo estable sobre 60 GHz (misma dirección AP -> STA que el vídeo reenviado)
IPERF_PORT=5201
IPERF_BITRATE="180M"
IPERF_INTERVAL=1
IPERF_DURATION=3600
IPERF_UDP_LENGTH=1470

# YOLO local USB: NO se envía por el enlace 60 GHz.
# 120p real = 160x120. El bitrate queda como objetivo/log por coherencia con la demo.
YOLO_CAMERA_DEVICE="/dev/video0"
YOLO_CAMERA_WIDTH=1280
YOLO_CAMERA_HEIGHT=720
YOLO_CAMERA_FPS=15
YOLO_PROCESS_FPS=5
YOLO_CAMERA_BITRATE_KBPS=2000
YOLO_TSIZE=256
YOLO_FP16="true"
YOLO_DEVICE="gpu"
YOLO_CONF="0.25"
YOLO_NMS="0.45"
YOLO_PERSON_MIN_SCORE="0.55"
YOLO_PERSON_MIN_HEIGHT_RATIO="0.35"
YOLO_PERSON_MIN_AREA_RATIO="0.04"
YOLO_PERSON_MIN_ASPECT_RATIO="1.15"
YOLO_EXP_FILE="${GO2_ROOT}/camera_yolo/YOLOX/exps/default/yolox_s.py"
YOLO_CKPT="${GO2_ROOT}/camera_yolo/Weights/yolox_s.pth"
CAMERA_JSON_OUT="${GO2_ROOT}/camera_yolo/outputs/live_camera_state.json"

# CSI externo
CSI_REMOTE_SCRIPT="/root/scripts_csi_dog/stream_csi_live_05s_single.sh"
CSI_STREAM_FILE="${GO2_ROOT}/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt"
CSI_STATE_JSON="${GO2_ROOT}/csi_dog_dataset_20210421_181125/analysis_outputs/live_prediction_state.json"
CSI_PREDICTIONS_CSV="${GO2_ROOT}/csi_dog_dataset_20210421_181125/analysis_outputs/live_predictions_external_stream.csv"

CSI_CONFIRM_COUNT="1"
CSI_VOTE_WINDOW="3"
CSI_VOTE_THRESHOLD="2"

# MAC del AP .13 por wlan0. Importante para el vendor recv CSI.

SSH_OPTS=(
  -o BatchMode=yes
  -o ConnectTimeout=5
  -o HostKeyAlgorithms=+ssh-rsa
  -o PubkeyAcceptedAlgorithms=+ssh-rsa
  -o StrictHostKeyChecking=accept-new
)

SSH_OPTS_STRING="-o BatchMode=yes -o ConnectTimeout=5 -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa -o StrictHostKeyChecking=accept-new"

log() {
  echo -e "\033[1;32m[mp4-demo]\033[0m $*"
}

warn() {
  echo -e "\033[1;33m[mp4-demo][WARN]\033[0m $*" >&2
}

err() {
  echo -e "\033[1;31m[mp4-demo][ERROR]\033[0m $*" >&2
}

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

open_terminal() {
  local title="$1"
  local command="$2"

  if command -v gnome-terminal >/dev/null 2>&1; then
    gnome-terminal --title="$title" -- bash -lc "$command; echo; echo '[${title}] terminado. ENTER para cerrar...'; read -r" &
  elif command -v xterm >/dev/null 2>&1; then
    xterm -T "$title" -e bash -lc "$command; echo; echo '[${title}] terminado. ENTER para cerrar...'; read -r" &
  elif command -v konsole >/dev/null 2>&1; then
    konsole --new-tab --workdir "$GO2_ROOT" -p tabtitle="$title" -e bash -lc "$command; echo; echo '[${title}] terminado. ENTER para cerrar...'; read -r" &
  else
    err "No encuentro gnome-terminal, xterm ni konsole."
    exit 1
  fi
}

ssh_run() {
  local host="$1"
  local user="$2"
  ssh "${SSH_OPTS[@]}" "${user}@${host}" "sh -s"
}

wait_for_ssh() {
  local host="$1"
  local user="$2"
  local label="$3"

  log "Comprobando SSH en ${label} ${host}..."

  for i in $(seq 1 30); do
    if ssh "${SSH_OPTS[@]}" "${user}@${host}" "echo ok" >/dev/null 2>&1; then
      log "SSH OK en ${label}"
      return 0
    fi
    warn "SSH no disponible en ${label}. Intento ${i}/30"
    sleep 1
  done

  err "No se pudo conectar a ${label} ${host}"
  exit 1
}

preflight() {
  check_cmd ssh
  check_cmd ip
  check_cmd python3
  check_file "$PROJECT_PYTHON"
  check_cmd tcpdump
  check_cmd timeout
  check_cmd gst-launch-1.0

  check_file "$PY_SCRIPT"
  check_file "$VIDEO_FILE"
  check_file "$CSI_LIVE_PREDICTOR"
  check_file "$CSI_MODEL"
  check_file "$YOLO_EXP_FILE"
  check_file "$YOLO_CKPT"

  sudo -v

  if [[ -z "${DISPLAY:-}" ]]; then
    err "DISPLAY no está definido. Necesitas sesión gráfica para las ventanas."
    exit 1
  fi
}

cleanup_local() {
  log "Limpiando procesos locales previos..."

  pkill -f "video_file_tx_rx_only.py" 2>/dev/null || true
  pkill -f "video_file_tx_rx_yolo_usb.py" 2>/dev/null || true
  pkill -f "video_file_tx_yolo_cam_dashboard.py" 2>/dev/null || true
    pkill -f "csi_live_predictor_from_stream.py" 2>/dev/null || true
  pkill -f "gst-launch-1.0" 2>/dev/null || true
  pkill -f "avdec_h264" 2>/dev/null || true
  pkill -f "x264enc" 2>/dev/null || true
  sudo fuser -k "${PC_RX_PORT}/udp" 2>/dev/null || true
  sudo fuser -k "${YOLO_CAMERA_DEVICE}" 2>/dev/null || true

  mkdir -p "$(dirname "$CSI_STREAM_FILE")"
  mkdir -p "$(dirname "$CSI_STATE_JSON")"
  : > "$CSI_STREAM_FILE"
  rm -f "$CSI_STATE_JSON" "$CSI_PREDICTIONS_CSV" 2>/dev/null || true

  log "Limpieza local OK."
}

cleanup_remote_csi() {
  log "Limpiando CSI remoto en STA..."

  ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" '
for pid in $(ps w | grep -E "stream_csi|vendor recv|iw dev wlan0 vendor|id_rsa_dropbear|ssh|dbclient" | grep -v grep | awk "{print \$1}"); do
  echo "[STA] kill -9 $pid"
  kill -9 "$pid" 2>/dev/null || true
done

killall -9 iw 2>/dev/null || true
pkill -9 -f "stream_csi" 2>/dev/null || true
pkill -9 -f "live_csi_stream" 2>/dev/null || true
pkill -9 -f "id_rsa_dropbear" 2>/dev/null || true
killall -9 ssh 2>/dev/null || true
killall -9 dbclient 2>/dev/null || true

rm -rf /tmp/csi_*.lock
rm -f /tmp/csi_stream_*.pid /tmp/csi_stream_*.out /tmp/csi_*local*.txt

dmesg -c >/tmp/dmesg_cleared_before_csi05.txt 2>/dev/null || true

echo "[STA] CSI limpio."
' || true
}

configure_local_ip_and_routes() {
  log "Configurando IP local ${LOCAL_VIDEO_IP} en ${LOCAL_IFACE}..."

  if ip -4 addr show dev "$LOCAL_IFACE" | grep -q "${LOCAL_VIDEO_IP_PLAIN}/24"; then
    log "IP local ya configurada."
  else
    sudo ip addr add "$LOCAL_VIDEO_IP" dev "$LOCAL_IFACE"
  fi

  sudo ip route replace "${AP_HOST}/32" dev "$LOCAL_IFACE" src "$LOCAL_VIDEO_IP_PLAIN" metric 1
  sudo ip route replace "${STA_HOST}/32" dev "$LOCAL_IFACE" src "$LOCAL_VIDEO_IP_PLAIN" metric 1
  sudo ip route flush cache

  log "Ruta AP:"
  ip route get "$AP_HOST" || true

  log "Ruta STA:"
  ip route get "$STA_HOST" || true
}

clean_radio_side() {
  local host="$1"
  local user="$2"
  local label="$3"

  log "Limpieza radio ${label}..."

  ssh_run "$host" "$user" <<'EOF'
set -u

killall -9 hostapd 2>/dev/null || true
killall -9 wpa_supplicant 2>/dev/null || true
killall -9 iw 2>/dev/null || true
pkill -9 -f "stream_csi" 2>/dev/null || true
pkill -9 -f "live_csi_stream" 2>/dev/null || true
pkill -9 -f "id_rsa_dropbear" 2>/dev/null || true
killall -9 iperf3 2>/dev/null || true
killall -9 tcpdump 2>/dev/null || true

pkill -9 -f "vendor recv" 2>/dev/null || true
pkill -9 -f "stream_csi" 2>/dev/null || true
pkill -9 -f "iw dev wlan0 vendor" 2>/dev/null || true

rm -rf /var/run/wpa_supplicant
mkdir -p /var/run/wpa_supplicant

ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
ip neigh flush dev wlan0 2>/dev/null || true

sleep 2

WILDIR="$(ls -d /sys/kernel/debug/ieee80211/phy*/wil6210 2>/dev/null | tail -n 1 || true)"
echo "WILDIR=$WILDIR"
[ -n "$WILDIR" ] && echo "FW=$(cat "$WILDIR/fw_version" 2>/dev/null || true)"

if [ -n "$WILDIR" ] && [ -w "$WILDIR/reset" ]; then
  echo 1 > "$WILDIR/reset" 2>/dev/null || true
  sleep 5
fi
EOF
}

write_radio_configs() {
  log "Escribiendo hostapd/wpa configs..."

  ssh_run "$AP_HOST" "$AP_USER" <<EOF
cat >/tmp/hostapd_60g_clean.conf <<'EOF_HOSTAPD'
interface=wlan0
driver=nl80211
ssid=${SSID_60G}
hw_mode=ad
channel=${CHANNEL_60G}
auth_algs=1
ignore_broadcast_ssid=0
EOF_HOSTAPD

cat /tmp/hostapd_60g_clean.conf
EOF

  ssh_run "$STA_HOST" "$STA_USER" <<EOF
cat >/tmp/wpa_60g_clean.conf <<'EOF_WPA'
ctrl_interface=/var/run/wpa_supplicant
ap_scan=1

network={
    ssid="${SSID_60G}"
    key_mgmt=NONE
    scan_ssid=1
    scan_freq=${FREQ_60G}
    freq_list=${FREQ_60G}
}
EOF_WPA

cat /tmp/wpa_60g_clean.conf
EOF
}

start_ap() {
  log "Arrancando AP .13..."

  ssh_run "$AP_HOST" "$AP_USER" <<EOF
set -u

ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
sleep 2

ip link set wlan0 up
sleep 5

ip addr add ${AP_WLAN_IP} dev wlan0 2>/dev/null || true

hostapd -B /tmp/hostapd_60g_clean.conf
sleep 6

echo "=== AP iw info ==="
iw dev wlan0 info || true

echo
echo "=== AP wlan0 ==="
ip addr show wlan0 || true

echo
echo "=== AP hostapd ==="
ps w | grep hostapd | grep -v grep || true
EOF
}

start_sta() {
  log "Arrancando STA .12..."

  ssh_run "$STA_HOST" "$STA_USER" <<EOF
set -u

killall -9 wpa_supplicant 2>/dev/null || true
rm -rf /var/run/wpa_supplicant
mkdir -p /var/run/wpa_supplicant

ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
sleep 2

ip link set wlan0 up
sleep 5

ip addr add ${STA_WLAN_IP} dev wlan0 2>/dev/null || true

wpa_supplicant -D nl80211 -i wlan0 -c /tmp/wpa_60g_clean.conf -B
sleep 3

for i in \$(seq 1 40); do
  if iw dev wlan0 link 2>/dev/null | grep -q "Connected to"; then
    echo "[STA] Conectada."
    break
  fi
  echo "[STA] Esperando asociación \$i/40"
  sleep 1
done

echo "=== STA iw link ==="
iw dev wlan0 link || true

echo
echo "=== STA wlan0 ==="
ip addr show wlan0 || true
EOF
}

wait_radio() {
  log "Verificando asociación y ping 60GHz..."

  for i in $(seq 1 30); do
    if ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" "iw dev wlan0 link | grep -q 'Connected to'" >/dev/null 2>&1; then
      log "STA asociada."
      break
    fi

    warn "STA aún no asociada ${i}/30"
    sleep 1
  done

  echo "=== STA -> AP ==="
  ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" "ping -I wlan0 -c 5 -W 2 ${AP_WLAN_IP_PLAIN}" || true

  echo
  echo "=== AP -> STA ==="
  ssh "${SSH_OPTS[@]}" "${AP_USER}@${AP_HOST}" "ping -I wlan0 -c 5 -W 2 ${STA_WLAN_IP_PLAIN}" || true
}

configure_forwarding() {
  


log "Comprobación estricta de ping AP/STA antes de forwarding..."

if ! ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" "ping -I wlan0 -c 5 -W 2 ${AP_WLAN_IP_PLAIN}" >/tmp/mp4_demo_ping_sta_to_ap.log 2>&1; then
  cat /tmp/mp4_demo_ping_sta_to_ap.log || true
  err "STA no puede hacer ping al AP por wlan0. Enlace 60GHz asociado pero NO operativo a nivel IP."
  return 1
fi

if ! ssh "${SSH_OPTS[@]}" "${AP_USER}@${AP_HOST}" "ping -I wlan0 -c 5 -W 2 ${STA_WLAN_IP_PLAIN}" >/tmp/mp4_demo_ping_ap_to_sta.log 2>&1; then
  cat /tmp/mp4_demo_ping_ap_to_sta.log || true
  err "AP no puede hacer ping a la STA por wlan0. Enlace 60GHz asociado pero NO operativo a nivel IP."
  exit 1
fi

log "Ping AP/STA OK. Continuando con forwarding."


log "Configurando forwarding AP/STA..."

  ssh_run "$AP_HOST" "$AP_USER" <<EOF
set -u

sysctl -w net.ipv4.ip_forward=1
iptables -P FORWARD ACCEPT

while iptables -t nat -D PREROUTING -i br-lan -p udp -d ${AP_HOST} --dport ${AP_VIDEO_PORT} -j DNAT --to-destination ${STA_WLAN_IP_PLAIN}:${AP_VIDEO_PORT} 2>/dev/null; do :; done
while iptables -t nat -D POSTROUTING -o wlan0 -p udp -d ${STA_WLAN_IP_PLAIN} --dport ${AP_VIDEO_PORT} -j SNAT --to-source ${AP_WLAN_IP_PLAIN} 2>/dev/null; do :; done
while iptables -D FORWARD -i br-lan -o wlan0 -p udp -d ${STA_WLAN_IP_PLAIN} --dport ${AP_VIDEO_PORT} -j ACCEPT 2>/dev/null; do :; done

iptables -t nat -I PREROUTING 1 -i br-lan -p udp -d ${AP_HOST} --dport ${AP_VIDEO_PORT} -j DNAT --to-destination ${STA_WLAN_IP_PLAIN}:${AP_VIDEO_PORT}
iptables -I FORWARD 1 -i br-lan -o wlan0 -p udp -d ${STA_WLAN_IP_PLAIN} --dport ${AP_VIDEO_PORT} -j ACCEPT
iptables -t nat -I POSTROUTING 1 -o wlan0 -p udp -d ${STA_WLAN_IP_PLAIN} --dport ${AP_VIDEO_PORT} -j SNAT --to-source ${AP_WLAN_IP_PLAIN}

iptables -t nat -L PREROUTING -v -n --line-numbers | head
iptables -L FORWARD -v -n --line-numbers | head
EOF

  ssh_run "$STA_HOST" "$STA_USER" <<EOF
set -u

sysctl -w net.ipv4.ip_forward=1
iptables -P FORWARD ACCEPT

while iptables -t nat -D PREROUTING -i wlan0 -p udp -d ${STA_WLAN_IP_PLAIN} --dport ${AP_VIDEO_PORT} -j DNAT --to-destination ${LOCAL_VIDEO_IP_PLAIN}:${PC_RX_PORT} 2>/dev/null; do :; done
while iptables -t nat -D POSTROUTING -o br-lan -p udp -d ${LOCAL_VIDEO_IP_PLAIN} --dport ${PC_RX_PORT} -j SNAT --to-source ${STA_HOST} 2>/dev/null; do :; done
while iptables -D FORWARD -i wlan0 -o br-lan -p udp -d ${LOCAL_VIDEO_IP_PLAIN} --dport ${PC_RX_PORT} -j ACCEPT 2>/dev/null; do :; done

iptables -t nat -I PREROUTING 1 -i wlan0 -p udp -d ${STA_WLAN_IP_PLAIN} --dport ${AP_VIDEO_PORT} -j DNAT --to-destination ${LOCAL_VIDEO_IP_PLAIN}:${PC_RX_PORT}
iptables -I FORWARD 1 -i wlan0 -o br-lan -p udp -d ${LOCAL_VIDEO_IP_PLAIN} --dport ${PC_RX_PORT} -j ACCEPT
iptables -t nat -I POSTROUTING 1 -o br-lan -p udp -d ${LOCAL_VIDEO_IP_PLAIN} --dport ${PC_RX_PORT} -j SNAT --to-source ${STA_HOST}

iptables -t nat -L PREROUTING -v -n --line-numbers | head
iptables -L FORWARD -v -n --line-numbers | head
EOF
}

verify_udp_path() {
  log "Verificando UDP path PC -> AP -> STA -> PC..."

  local pcap="/tmp/mp4demo_udp_path_check.pcap"
  sudo rm -f "$pcap"

  sudo timeout 8 tcpdump -ni "$LOCAL_IFACE" "udp and port ${PC_RX_PORT}" -w "$pcap" &
  local tcpdump_pid=$!

  sleep 1

  python3 - <<PY
import socket, time
dst = ("${AP_HOST}", ${AP_VIDEO_PORT})
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
payload = b"x" * 1200
for i in range(1000):
    s.sendto(payload, dst)
    time.sleep(0.002)
print("sent UDP test packets to", dst)
PY

  wait "$tcpdump_pid" 2>/dev/null || true

  local count
  count="$(sudo tcpdump -r "$pcap" 2>/dev/null | wc -l || echo 0)"

  log "Paquetes capturados en PC:${PC_RX_PORT}: ${count}"

  if [[ "$count" -lt 10 ]]; then
    err "No llega tráfico UDP reenviado a PC:${PC_RX_PORT}"
    exit 1
  fi
}

install_remote_csi05_script() {
  log "Instalando streamer CSI 0.5s del repositorio en STA..."

  local script_dir
  local repo_root
  local local_script

  script_dir="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  repo_root="$(cd "${script_dir}/.." && pwd)"
  local_script="${repo_root}/deployment/openwrt/scripts_csi_dog/stream_csi_live_05s_single.sh"

  check_file "${local_script}"

  ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" "mkdir -p /root/scripts_csi_dog"
  cat "${local_script}" | ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" "cat > '${CSI_REMOTE_SCRIPT}' && chmod +x '${CSI_REMOTE_SCRIPT}'"

  log "Streamer CSI desplegado en ${STA_HOST}:${CSI_REMOTE_SCRIPT}"
}

start_remote_csi05() {
  log "Arrancando CSI 0.5s en STA..."

  cleanup_remote_csi

  ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" "
rm -rf /tmp/csi_05s_single.lock
rm -f /tmp/csi_05s_single.out /tmp/csi_05s_single.pid

(


  sh '${CSI_REMOTE_SCRIPT}' '${LOCAL_VIDEO_IP_PLAIN}' '${USER}' '${CSI_STREAM_FILE}'
) >/tmp/csi_05s_single.out 2>&1 &

echo \$! > /tmp/csi_05s_single.pid
echo CSI05_PID=\$(cat /tmp/csi_05s_single.pid)
"

  sleep 2

  ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" '
echo "=== CSI 0.5 PID ==="
cat /tmp/csi_05s_single.pid 2>/dev/null || true

echo
echo "=== procesos CSI ==="
ps w | grep -E "stream_csi|vendor recv|ssh|dbclient" | grep -v grep || true

echo
echo "=== log inicial CSI ==="
tail -n 50 /tmp/csi_05s_single.out 2>/dev/null || true
'
}

verify_csi_arrives() {
  log "Verificando llegada de CSI a Spark..."

  local before after
  before="$(wc -l < "$CSI_STREAM_FILE" 2>/dev/null || echo 0)"

  for i in $(seq 1 20); do
    sleep 1
    after="$(wc -l < "$CSI_STREAM_FILE" 2>/dev/null || echo 0)"

    if [[ "$after" -gt "$before" ]]; then
      log "CSI OK: ${before} -> ${after} líneas"
      tail -n 5 "$CSI_STREAM_FILE" || true
      return 0
    fi

    warn "Esperando CSI ${i}/20"
  done

  err "No llegan CSI al fichero ${CSI_STREAM_FILE}"
  ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" "tail -n 120 /tmp/csi_05s_single.out 2>/dev/null || true" || true
  exit 1
}

launch_csi_predictor() {
  log "Lanzando predictor CSI live..."

  open_terminal "T3 - CSI Predictor 0.5s" "
set -e
cd '${GO2_ROOT}'

'${PROJECT_PYTHON}' '${CSI_LIVE_PREDICTOR}' \
  --input-file '${CSI_STREAM_FILE}' \
  --model '${CSI_MODEL}' \
  --state-json '${CSI_STATE_JSON}' \
  --predictions-csv '${CSI_PREDICTIONS_CSV}' \
  --confirm-count '${CSI_CONFIRM_COUNT}' \
  --vote-window '${CSI_VOTE_WINDOW}' \
  --vote-threshold '${CSI_VOTE_THRESHOLD}'
"
}

launch_video_demo() {
  log "Lanzando vídeo MP4 TX/RX + YOLO USB local 240p..."

  open_terminal "T4 - VIDEO 20 Mbps + YOLO USB 240p" "
set -e
cd '${GO2_ROOT}'

mkdir -p '${GO2_ROOT}/debug_logs'
LOG_FILE='${GO2_ROOT}/debug_logs/video_20mbps_yolo_usb_'\$(date +%Y%m%d_%H%M%S)'.log'

'${PROJECT_PYTHON}' '${PY_SCRIPT}' \
  --video-file '${VIDEO_FILE}' \
  --local-iface '${LOCAL_IFACE}' \
  --local-video-ip '${LOCAL_VIDEO_IP_PLAIN}' \
  --ap-eth-ip '${AP_HOST}' \
  --ap-eth-port '${AP_VIDEO_PORT}' \
  --pc-rx-port '${PC_RX_PORT}' \
  --tx-width '${TX_WIDTH}' \
  --tx-height '${TX_HEIGHT}' \
  --tx-fps '${TX_FPS}' \
  --bitrate-kbps '${BITRATE_KBPS}' \
  --buffer-ms '${BUFFER_MS}' \
  --rtp-mtu '${RTP_MTU}' \
  --vbv-ms '${VBV_MS}' \
  --enable-yolo-camera \
  --yolo-camera-device '${YOLO_CAMERA_DEVICE}' \
  --yolo-camera-width '${YOLO_CAMERA_WIDTH}' \
  --yolo-camera-height '${YOLO_CAMERA_HEIGHT}' \
  --yolo-camera-fps '${YOLO_CAMERA_FPS}' \
  --yolo-process-fps '${YOLO_PROCESS_FPS}' \
  --yolo-camera-bitrate-kbps '${YOLO_CAMERA_BITRATE_KBPS}' \
  --camera-json-out '${CAMERA_JSON_OUT}' \
  --yolo-exp-file '${YOLO_EXP_FILE}' \
  --yolo-ckpt '${YOLO_CKPT}' \
  --yolo-device '${YOLO_DEVICE}' \
  --yolo-conf '${YOLO_CONF}' \
  --yolo-nms '${YOLO_NMS}' \
  --yolo-person-min-score '${YOLO_PERSON_MIN_SCORE}' \
  --yolo-person-min-height-ratio '${YOLO_PERSON_MIN_HEIGHT_RATIO}' \
  --yolo-person-min-area-ratio '${YOLO_PERSON_MIN_AREA_RATIO}' \
  --yolo-person-min-aspect-ratio '${YOLO_PERSON_MIN_ASPECT_RATIO}' \
  --yolo-tsize '${YOLO_TSIZE}' \
  $([[ '${YOLO_FP16}' == 'true' ]] && echo '--yolo-fp16') \
  --verbose 2>&1 | tee -a \"\${LOG_FILE}\"
"
}

start_iperf_150m() {
  log "Lanzando iperf3 UDP estable paralelo: AP ${AP_WLAN_IP_PLAIN} -> STA ${STA_WLAN_IP_PLAIN}, ${IPERF_BITRATE}"

  ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" "
killall -9 iperf3 2>/dev/null || true
iperf3 -s -p '${IPERF_PORT}' -1 >/tmp/iperf3_server_udp180.out 2>&1 &
echo \$! >/tmp/iperf3_server_udp180.pid
echo '[STA] iperf3 server PID='\"\$(cat /tmp/iperf3_server_udp180.pid)\"
"

  sleep 1

  open_terminal "T5 - iperf3 UDP 180 Mbps AP->STA" "
set -e
echo '[iperf] Cliente UDP en AP -> servidor STA por wlan0'
echo '[iperf] Target: ${STA_WLAN_IP_PLAIN}:${IPERF_PORT}, bitrate=${IPERF_BITRATE}'
ssh ${SSH_OPTS_STRING} ${AP_USER}@${AP_HOST} \
  \"iperf3 -u -c '${STA_WLAN_IP_PLAIN}' -B '${AP_WLAN_IP_PLAIN}' -b '${IPERF_BITRATE}' -l '${IPERF_UDP_LENGTH}' -i '${IPERF_INTERVAL}' -p '${IPERF_PORT}' -t '${IPERF_DURATION}'\"
"
}


configure_forwarding_with_radio_retry() {
  local attempts=3

  for attempt in $(seq 1 "$attempts"); do
    log "Comprobando forwarding con enlace IP real. Intento ${attempt}/${attempts}..."

    if configure_forwarding; then
      log "Forwarding configurado con enlace AP/STA operativo."
      return 0
    fi

    warn "El enlace aparece asociado, pero no hay ping IP. Reiniciando radio AP/STA..."

    clean_radio_side "$AP_HOST" "$AP_USER" "AP" || true
    clean_radio_side "$STA_HOST" "$STA_USER" "STA" || true

    write_radio_configs || true

    start_ap
    start_sta

    sleep 3
  done

  err "No se pudo conseguir enlace AP/STA operativo tras ${attempts} intentos."
  exit 1
}

prepare_remote_csi_mac_bin() {
  log "Preparando MAC binaria CSI en STA..."

  ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" '
set -u

rm -f /tmp/ap_mac.bin
echo -n -e '"'"'\xb8\x69\xf4\xd5\x49\xa9'"'"' > /tmp/ap_mac.bin

BYTES="$(wc -c < /tmp/ap_mac.bin)"
if [ "$BYTES" != "6" ]; then
  echo "[STA][ERROR] /tmp/ap_mac.bin no tiene 6 bytes, tiene $BYTES"
  hexdump -C /tmp/ap_mac.bin 2>/dev/null || od -An -tx1 /tmp/ap_mac.bin
  exit 1
fi

echo "=== /tmp/ap_mac.bin ==="
hexdump -C /tmp/ap_mac.bin 2>/dev/null || od -An -tx1 /tmp/ap_mac.bin
'
}




# ============================================================
# DEFINITIVE RADIO HEALTH CHECK + RETRY
# Association is not enough. We require bidirectional IP ping.
# ============================================================

radio_ping_strict_ok() {
  echo "[mp4-demo] Comprobación estricta de ping AP/STA antes de forwarding..."

  if ! ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" "ping -I wlan0 -c 5 -W 2 ${AP_WLAN_IP_PLAIN}" >/tmp/mp4demo_sta_to_ap_ping.log 2>&1; then
    cat /tmp/mp4demo_sta_to_ap_ping.log || true
    return 1
  fi

  if ! ssh "${SSH_OPTS[@]}" "${AP_USER}@${AP_HOST}" "ping -I wlan0 -c 5 -W 2 ${STA_WLAN_IP_PLAIN}" >/tmp/mp4demo_ap_to_sta_ping.log 2>&1; then
    cat /tmp/mp4demo_ap_to_sta_ping.log || true
    return 1
  fi

  if ! grep -q "0% packet loss" /tmp/mp4demo_sta_to_ap_ping.log; then
    cat /tmp/mp4demo_sta_to_ap_ping.log || true
    return 1
  fi

  if ! grep -q "0% packet loss" /tmp/mp4demo_ap_to_sta_ping.log; then
    cat /tmp/mp4demo_ap_to_sta_ping.log || true
    return 1
  fi

  echo "[mp4-demo] Ping AP/STA OK. Continuando con forwarding."
  return 0
}

radio_soft_reset_both() {
  echo "[mp4-demo][WARN] Enlace asociado pero sin IP. Reset radio AP/STA..."

  for side in "AP:${AP_USER}:${AP_HOST}" "STA:${STA_USER}:${STA_HOST}"; do
    IFS=: read -r label user host <<<"$side"

    echo "[mp4-demo] Reset radio $label $host"

    ssh "${SSH_OPTS[@]}" "${user}@${host}" '
killall -9 hostapd 2>/dev/null || true
killall -9 wpa_supplicant 2>/dev/null || true
killall -9 iw 2>/dev/null || true
killall -9 iperf3 2>/dev/null || true
killall -9 tcpdump 2>/dev/null || true
pkill -9 -f "stream_csi" 2>/dev/null || true
pkill -9 -f "vendor recv" 2>/dev/null || true
pkill -9 -f "iw dev wlan0 vendor" 2>/dev/null || true

rm -rf /var/run/wpa_supplicant
mkdir -p /var/run/wpa_supplicant

ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
ip neigh flush dev wlan0 2>/dev/null || true

sleep 2

WILDIR=$(ls -d /sys/kernel/debug/ieee80211/phy*/wil6210 2>/dev/null | tail -n 1 || true)
echo "WILDIR=$WILDIR"
[ -n "$WILDIR" ] && echo "FW=$(cat "$WILDIR/fw_version" 2>/dev/null)"
[ -n "$WILDIR" ] && [ -w "$WILDIR/reset" ] && echo 1 > "$WILDIR/reset" 2>/dev/null || true

sleep 6
dmesg -c >/tmp/dmesg_after_radio_soft_reset.txt 2>/dev/null || true
' || true
  done
}




main() {
  log "Preflight..."
  preflight

  log "Limpieza local..."
  cleanup_local

  log "Configurando IP/rutas locales..."
  configure_local_ip_and_routes

  wait_for_ssh "$AP_HOST" "$AP_USER" "AP"
  wait_for_ssh "$STA_HOST" "$STA_USER" "STA"

  log "Preparando enlace 60GHz..."
  clean_radio_side "$AP_HOST" "$AP_USER" "AP"
  clean_radio_side "$STA_HOST" "$STA_USER" "STA"
  write_radio_configs
  start_ap
  start_sta
  wait_radio
  configure_forwarding_with_radio_retry
  verify_udp_path

  # Keep the MikroTik runtime script in sync with the version tracked in deployment/.
  install_remote_csi05_script
  prepare_remote_csi_mac_bin
  start_remote_csi05
  verify_csi_arrives

  launch_csi_predictor
  sleep 2

  launch_video_demo
  sleep 1

  start_iperf_150m
  sleep 1

  # open_antenna_shells desactivado: función no definida en esta versión.

  log "Todo lanzado."
  log "Vídeo MP4 TX/RX: ${TX_WIDTH}x${TX_HEIGHT}@${TX_FPS}, $((BITRATE_KBPS/1000)) Mbps"
  log "YOLO USB local: ${YOLO_CAMERA_DEVICE}, ${YOLO_CAMERA_WIDTH}x${YOLO_CAMERA_HEIGHT}@${YOLO_CAMERA_FPS}, objetivo $((YOLO_CAMERA_BITRATE_KBPS/1000)) Mbps"
  log "CSI: 0.5s desde STA .12 hacia ${CSI_STREAM_FILE}"
  log "iperf3 UDP estable paralelo: ${IPERF_BITRATE} AP ${AP_WLAN_IP_PLAIN} -> STA ${STA_WLAN_IP_PLAIN}"
}

main "$@"
