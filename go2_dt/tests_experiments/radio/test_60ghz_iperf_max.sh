#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# 60 GHz iperf3 maximum throughput test
#
# Ejecutar en Spark:
#   cd ${GO2_ROOT}
#   ./tests_experiments/radio/test_60ghz_iperf_max.sh
#
# Objetivo:
#   Configurar AP/STA 60 GHz y medir throughput máximo con iperf3.
#
# Topología:
#   Spark/PC --eth--> AP 192.168.1.13
#   Spark/PC --eth--> STA 192.168.1.12
#   AP wlan0:  10.10.10.1/24
#   STA wlan0: 10.10.10.2/24
#
# Pruebas:
#   TCP AP -> STA
#   TCP STA -> AP, usando iperf3 -R
#   UDP sweep opcional
# ============================================================

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GO2_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

LOCAL_IFACE="${LOCAL_IFACE:-enP7s7}"
LOCAL_IP="${LOCAL_IP:-192.168.1.170/24}"
LOCAL_IP_PLAIN="${LOCAL_IP_PLAIN:-192.168.1.170}"

AP_HOST="${AP_HOST:-192.168.1.13}"
STA_HOST="${STA_HOST:-192.168.1.12}"

AP_USER="${AP_USER:-root}"
STA_USER="${STA_USER:-root}"

AP_WLAN_IP="${AP_WLAN_IP:-10.10.10.1/24}"
AP_WLAN_IP_PLAIN="${AP_WLAN_IP_PLAIN:-10.10.10.1}"

STA_WLAN_IP="${STA_WLAN_IP:-10.10.10.2/24}"
STA_WLAN_IP_PLAIN="${STA_WLAN_IP_PLAIN:-10.10.10.2}"

SSID_60G="${SSID_60G:-TEST-LINK}"
CHANNEL_60G="${CHANNEL_60G:-2}"
FREQ_60G="${FREQ_60G:-60480}"

IPERF_PORT="${IPERF_PORT:-5201}"
TCP_TIME="${TCP_TIME:-20}"
TCP_PARALLEL="${TCP_PARALLEL:-1}"

# Barrido UDP opcional.
# Pon RUN_UDP=1 para probar UDP.
RUN_UDP="${RUN_UDP:-0}"
UDP_TIME="${UDP_TIME:-10}"
UDP_RATES="${UDP_RATES:-100M 150M 200M 300M 400M 500M 700M 900M}"

LOG_DIR="${GO2_ROOT}/tests_experiments/tmp_outputs/iperf_60ghz_$(date +%Y%m%d_%H%M%S)"

SSH_OPTS=(
  -o HostKeyAlgorithms=+ssh-rsa
  -o PubkeyAcceptedAlgorithms=+ssh-rsa
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=5
)

log() {
  echo -e "\033[1;32m[iperf-60g]\033[0m $*"
}

warn() {
  echo -e "\033[1;33m[iperf-60g][WARN]\033[0m $*" >&2
}

err() {
  echo -e "\033[1;31m[iperf-60g][ERROR]\033[0m $*" >&2
}

ssh_cmd() {
  local user="$1"
  local host="$2"
  shift 2
  ssh "${SSH_OPTS[@]}" "${user}@${host}" "$@"
}

ssh_sh() {
  local user="$1"
  local host="$2"
  ssh "${SSH_OPTS[@]}" "${user}@${host}" "sh -s"
}

wait_for_ssh() {
  local user="$1"
  local host="$2"
  local label="$3"

  log "Comprobando SSH ${label} (${host})..."

  for i in $(seq 1 25); do
    if ssh "${SSH_OPTS[@]}" "${user}@${host}" "echo ok" >/dev/null 2>&1; then
      log "SSH OK en ${label}"
      return 0
    fi

    warn "SSH no disponible en ${label}. Intento ${i}/25"
    sleep 1
  done

  err "No se pudo conectar por SSH a ${label}"
  exit 1
}

check_local_requirements() {
  command -v ssh >/dev/null || { err "ssh no encontrado"; exit 1; }
  command -v ip >/dev/null || { err "ip no encontrado"; exit 1; }
  command -v tee >/dev/null || { err "tee no encontrado"; exit 1; }

  mkdir -p "${LOG_DIR}"
  log "Log dir: ${LOG_DIR}"
}

configure_local_routes() {
  log "Configurando IP local y rutas hacia AP/STA..."

  sudo ip addr add "${LOCAL_IP}" dev "${LOCAL_IFACE}" 2>/dev/null || true
  sudo ip route replace "${AP_HOST}/32" dev "${LOCAL_IFACE}" src "${LOCAL_IP_PLAIN}" metric 1
  sudo ip route replace "${STA_HOST}/32" dev "${LOCAL_IFACE}" src "${LOCAL_IP_PLAIN}" metric 1
  sudo ip route flush cache

  echo
  echo "=== Ruta AP ==="
  ip route get "${AP_HOST}" || true

  echo
  echo "=== Ruta STA ==="
  ip route get "${STA_HOST}" || true
}

clean_radio_side() {
  local user="$1"
  local host="$2"
  local label="$3"

  log "Limpiando ${label}..."

  ssh_sh "${user}" "${host}" <<'EOSH'
set -u

echo "=== procesos antes ==="
ps w | grep -E "hostapd|wpa_supplicant|iperf3|iperf|tcpdump|iw dev wlan0 vendor|vendor recv|stream_csi" | grep -v grep || true

for pid in $(ps w | grep -E "hostapd|wpa_supplicant|iperf3|iperf|tcpdump|iw dev wlan0 vendor|vendor recv|stream_csi" | grep -v grep | awk "{print \$1}"); do
  echo "kill -9 $pid"
  kill -9 "$pid" 2>/dev/null || true
done

killall -9 hostapd 2>/dev/null || true
killall -9 wpa_supplicant 2>/dev/null || true
killall -9 iperf3 2>/dev/null || true
killall -9 iperf 2>/dev/null || true
killall -9 tcpdump 2>/dev/null || true
killall -9 iw 2>/dev/null || true

pkill -9 -f "iw dev wlan0 vendor" 2>/dev/null || true
pkill -9 -f "vendor recv" 2>/dev/null || true
pkill -9 -f "stream_csi" 2>/dev/null || true

rm -f /var/run/wpa_supplicant/wlan0 2>/dev/null || true
rm -rf /var/run/wpa_supplicant 2>/dev/null || true
mkdir -p /var/run/wpa_supplicant 2>/dev/null || true

ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
ip neigh flush dev wlan0 2>/dev/null || true

sleep 2

WILDIR="$(ls -d /sys/kernel/debug/ieee80211/phy*/wil6210 2>/dev/null | tail -n 1 || true)"
echo "WILDIR=$WILDIR"

if [ -n "$WILDIR" ]; then
  echo "[fw]"
  cat "$WILDIR/fw_version" 2>/dev/null || true
  echo "[recovery_count]"
  cat "$WILDIR/recovery_count" 2>/dev/null || true
fi

if [ -n "$WILDIR" ] && [ -w "$WILDIR/reset" ]; then
  echo "[clean] reset wil6210"
  echo 1 > "$WILDIR/reset" 2>/dev/null || true
  sleep 5
fi

echo "=== procesos después ==="
ps w | grep -E "hostapd|wpa_supplicant|iperf3|iperf|tcpdump|iw dev wlan0 vendor|vendor recv|stream_csi" | grep -v grep || true
EOSH
}

write_radio_configs() {
  log "Escribiendo configuración AP/STA..."

  ssh_sh "${AP_USER}" "${AP_HOST}" <<EOF_AP
cat >/tmp/hostapd_60g_iperf.conf <<EOF_CONF
interface=wlan0
driver=nl80211
ssid=${SSID_60G}
hw_mode=ad
channel=${CHANNEL_60G}
auth_algs=1
ignore_broadcast_ssid=0
EOF_CONF

echo "=== /tmp/hostapd_60g_iperf.conf ==="
cat /tmp/hostapd_60g_iperf.conf
EOF_AP

  ssh_sh "${STA_USER}" "${STA_HOST}" <<EOF_STA
cat >/tmp/wpa_60g_iperf.conf <<EOF_CONF
ctrl_interface=/var/run/wpa_supplicant
ap_scan=1

network={
    ssid="${SSID_60G}"
    key_mgmt=NONE
    scan_ssid=1
    scan_freq=${FREQ_60G}
    freq_list=${FREQ_60G}
}
EOF_CONF

echo "=== /tmp/wpa_60g_iperf.conf ==="
cat /tmp/wpa_60g_iperf.conf
EOF_STA
}

start_ap() {
  log "Arrancando AP 60 GHz..."

  ssh_sh "${AP_USER}" "${AP_HOST}" <<EOF_AP
set -u

ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
ip neigh flush dev wlan0 2>/dev/null || true

sleep 2
ip link set wlan0 up
sleep 5

ip addr add ${AP_WLAN_IP} dev wlan0 2>/dev/null || true

hostapd -B /tmp/hostapd_60g_iperf.conf
sleep 8

echo "=== AP wlan0 ==="
ip addr show wlan0 || true

echo
echo "=== AP iw info ==="
iw dev wlan0 info || true

echo
echo "=== AP hostapd ==="
ps w | grep hostapd | grep -v grep || true

echo
echo "=== AP stations ==="
WILDIR=\$(ls -d /sys/kernel/debug/ieee80211/phy*/wil6210 2>/dev/null | tail -n 1 || true)
echo "WILDIR=\$WILDIR"
[ -n "\$WILDIR" ] && cat "\$WILDIR/stations" 2>/dev/null || true
EOF_AP
}

start_sta() {
  log "Arrancando STA 60 GHz..."

  ssh_sh "${STA_USER}" "${STA_HOST}" <<EOF_STA
set -u

rm -f /var/run/wpa_supplicant/wlan0 2>/dev/null || true
rm -rf /var/run/wpa_supplicant 2>/dev/null || true
mkdir -p /var/run/wpa_supplicant 2>/dev/null || true

ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
ip neigh flush dev wlan0 2>/dev/null || true

sleep 2
ip link set wlan0 up
sleep 5

ip addr add ${STA_WLAN_IP} dev wlan0 2>/dev/null || true

wpa_supplicant -D nl80211 -i wlan0 -c /tmp/wpa_60g_iperf.conf -B
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
EOF_STA
}

check_link() {
  log "Verificando asociación y ping..."

  for i in $(seq 1 30); do
    if ssh_cmd "${STA_USER}" "${STA_HOST}" "iw dev wlan0 link | grep -q 'Connected to'" >/dev/null 2>&1; then
      log "STA asociada."
      break
    fi

    warn "STA aún no asociada ${i}/30"
    sleep 1
  done

  echo
  echo "=== STA -> AP ping ==="
  ssh_cmd "${STA_USER}" "${STA_HOST}" "ping -I wlan0 -c 5 -W 2 ${AP_WLAN_IP_PLAIN}" | tee "${LOG_DIR}/ping_sta_to_ap.txt" || true

  echo
  echo "=== AP -> STA ping ==="
  ssh_cmd "${AP_USER}" "${AP_HOST}" "ping -I wlan0 -c 5 -W 2 ${STA_WLAN_IP_PLAIN}" | tee "${LOG_DIR}/ping_ap_to_sta.txt" || true
}

check_iperf3() {
  log "Comprobando iperf3 en AP/STA..."

  ssh_cmd "${AP_USER}" "${AP_HOST}" "command -v iperf3 || command -v iperf" | tee "${LOG_DIR}/iperf_ap_version.txt" || {
    err "iperf3/iperf no está disponible en AP"
    exit 1
  }

  ssh_cmd "${STA_USER}" "${STA_HOST}" "command -v iperf3 || command -v iperf" | tee "${LOG_DIR}/iperf_sta_version.txt" || {
    err "iperf3/iperf no está disponible en STA"
    exit 1
  }

  ssh_cmd "${AP_USER}" "${AP_HOST}" "iperf3 --version 2>/dev/null | head -n 1 || true" | tee -a "${LOG_DIR}/iperf_ap_version.txt" || true
  ssh_cmd "${STA_USER}" "${STA_HOST}" "iperf3 --version 2>/dev/null | head -n 1 || true" | tee -a "${LOG_DIR}/iperf_sta_version.txt" || true
}

start_iperf_server_sta() {
  log "Arrancando iperf3 server en STA ${STA_WLAN_IP_PLAIN}:${IPERF_PORT}..."

  ssh_cmd "${STA_USER}" "${STA_HOST}" "
killall -9 iperf3 2>/dev/null || true
pkill -9 -f iperf3 2>/dev/null || true

iperf3 -s -B ${STA_WLAN_IP_PLAIN} -p ${IPERF_PORT} >/tmp/iperf3_server_sta_${IPERF_PORT}.log 2>&1 &

echo \$! >/tmp/iperf3_server_sta_${IPERF_PORT}.pid
sleep 1

echo '=== STA iperf3 server ==='
ps w | grep iperf3 | grep -v grep || true
tail -n 20 /tmp/iperf3_server_sta_${IPERF_PORT}.log 2>/dev/null || true
"
}

run_tcp_tests() {
  log "TCP AP -> STA. Duración=${TCP_TIME}s, parallel=${TCP_PARALLEL}"

  echo
  echo "============================================================"
  echo "TCP AP -> STA"
  echo "============================================================"

  ssh_cmd "${AP_USER}" "${AP_HOST}" "
iperf3 -c ${STA_WLAN_IP_PLAIN} \
  -p ${IPERF_PORT} \
  -t ${TCP_TIME} \
  -i 1 \
  -P ${TCP_PARALLEL}
" | tee "${LOG_DIR}/tcp_ap_to_sta_P${TCP_PARALLEL}.txt"

  sleep 2

  log "TCP STA -> AP usando iperf3 reverse (-R). Duración=${TCP_TIME}s, parallel=${TCP_PARALLEL}"

  echo
  echo "============================================================"
  echo "TCP STA -> AP reverse"
  echo "============================================================"

  ssh_cmd "${AP_USER}" "${AP_HOST}" "
iperf3 -c ${STA_WLAN_IP_PLAIN} \
  -p ${IPERF_PORT} \
  -t ${TCP_TIME} \
  -i 1 \
  -P ${TCP_PARALLEL} \
  -R
" | tee "${LOG_DIR}/tcp_sta_to_ap_reverse_P${TCP_PARALLEL}.txt"
}

run_tcp_parallel_sweep() {
  log "Barrido TCP paralelo: P=1,2,4,8"

  for p in 1 2 4 8; do
    echo
    echo "============================================================"
    echo "TCP AP -> STA, parallel=${p}"
    echo "============================================================"

    ssh_cmd "${AP_USER}" "${AP_HOST}" "
iperf3 -c ${STA_WLAN_IP_PLAIN} \
  -p ${IPERF_PORT} \
  -t ${TCP_TIME} \
  -i 1 \
  -P ${p}
" | tee "${LOG_DIR}/tcp_ap_to_sta_P${p}.txt"

    sleep 2
  done
}

run_udp_sweep() {
  if [[ "${RUN_UDP}" != "1" ]]; then
    log "RUN_UDP=0, saltando barrido UDP."
    return 0
  fi

  log "Barrido UDP AP -> STA: ${UDP_RATES}"

  for rate in ${UDP_RATES}; do
    echo
    echo "============================================================"
    echo "UDP AP -> STA, target=${rate}"
    echo "============================================================"

    ssh_cmd "${AP_USER}" "${AP_HOST}" "
iperf3 -c ${STA_WLAN_IP_PLAIN} \
  -p ${IPERF_PORT} \
  -u \
  -b ${rate} \
  -t ${UDP_TIME} \
  -i 1
" | tee "${LOG_DIR}/udp_ap_to_sta_${rate}.txt" || true

    sleep 2
  done
}

summarize_results() {
  log "Resumen rápido de resultados"

  echo
  echo "============================================================"
  echo "SUMMARY"
  echo "============================================================"

  echo "Logs: ${LOG_DIR}"
  echo

  echo "TCP sender/receiver summaries:"
  grep -R "sender\|receiver" "${LOG_DIR}"/tcp_*.txt 2>/dev/null || true

  echo
  echo "UDP summaries:"
  grep -R "receiver\|lost\|Jitter" "${LOG_DIR}"/udp_*.txt 2>/dev/null || true

  echo
  echo "Para revisar completo:"
  echo "  less ${LOG_DIR}/tcp_ap_to_sta_P1.txt"
  echo "  less ${LOG_DIR}/tcp_sta_to_ap_reverse_P${TCP_PARALLEL}.txt"
}

stop_iperf_only() {
  log "Parando iperf3 en AP/STA..."

  ssh_cmd "${AP_USER}" "${AP_HOST}" "killall -9 iperf3 2>/dev/null || true; pkill -9 -f iperf3 2>/dev/null || true" || true
  ssh_cmd "${STA_USER}" "${STA_HOST}" "killall -9 iperf3 2>/dev/null || true; pkill -9 -f iperf3 2>/dev/null || true" || true
}

main() {
  log "60 GHz iperf3 maximum throughput test"
  log "TCP_TIME=${TCP_TIME}, TCP_PARALLEL=${TCP_PARALLEL}, RUN_UDP=${RUN_UDP}"

  check_local_requirements
  configure_local_routes

  wait_for_ssh "${AP_USER}" "${AP_HOST}" "AP"
  wait_for_ssh "${STA_USER}" "${STA_HOST}" "STA"

  clean_radio_side "${AP_USER}" "${AP_HOST}" "AP"
  clean_radio_side "${STA_USER}" "${STA_HOST}" "STA"

  write_radio_configs
  start_ap
  start_sta
  check_link

  check_iperf3
  start_iperf_server_sta

  run_tcp_tests
  run_tcp_parallel_sweep
  run_udp_sweep

  summarize_results

  log "Prueba terminada. El enlace AP/STA queda levantado."
  log "Para parar solo iperf: ssh root@${AP_HOST} 'killall -9 iperf3'; ssh root@${STA_HOST} 'killall -9 iperf3'"
}

main "$@"
