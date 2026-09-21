#!/usr/bin/env bash
set -u

GO2_ROOT="/home/nextnet/AlbertoDir/go2_dt"

BASE_SETUP="${BASE_SETUP:-${GO2_ROOT}/tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_base.sh}"

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

AP_MGMT_HOST="${AP_MGMT_HOST:-192.168.1.13}"
STA_MGMT_HOST="${STA_MGMT_HOST:-192.168.1.12}"

SPARK_IFACE="${SPARK_IFACE:-enP7s7}"
SPARK_DEMO_IP="${SPARK_DEMO_IP:-172.16.12.170}"
RASPI_DEMO_IP="${RASPI_DEMO_IP:-172.16.13.100}"

AP_RADIO_IP="${AP_RADIO_IP:-10.10.10.1}"
STA_RADIO_IP="${STA_RADIO_IP:-10.10.10.2}"

MAX_RADIO_RETRIES="${MAX_RADIO_RETRIES:-5}"

log() {
  echo -e "\033[1;32m[setup-robust-v2]\033[0m $*"
}

warn() {
  echo -e "\033[1;33m[setup-robust-v2][WARN]\033[0m $*" >&2
}

err() {
  echo -e "\033[1;31m[setup-robust-v2][ERROR]\033[0m $*" >&2
}

ap_sh() {
  local script="$1"
  timeout 45 ssh \
    -oBatchMode=yes \
    -oConnectTimeout=8 \
    -oServerAliveInterval=5 \
    -oServerAliveCountMax=2 \
    -oHostKeyAlgorithms=+ssh-rsa \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    root@"${AP_MGMT_HOST}" 'sh -s' <<< "$script"
}

sta_sh() {
  local script="$1"
  timeout 45 ssh \
    -oBatchMode=yes \
    -oConnectTimeout=8 \
    -oServerAliveInterval=5 \
    -oServerAliveCountMax=2 \
    "${RASPI_USER}@${RASPI_MGMT_HOST}" \
    "ssh -oBatchMode=yes -oConnectTimeout=8 -oServerAliveInterval=5 -oServerAliveCountMax=2 -oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedAlgorithms=+ssh-rsa root@${STA_MGMT_HOST} 'sh -s'" <<< "$script"
}

raspi_sh() {
  local script="$1"
  timeout 35 ssh \
    -oBatchMode=yes \
    -oConnectTimeout=8 \
    -oServerAliveInterval=5 \
    -oServerAliveCountMax=2 \
    "${RASPI_USER}@${RASPI_MGMT_HOST}" 'sh -s' <<< "$script"
}

radio_ok() {
  log "Check radio AP13 <-> STA12..."

  sta_sh "
iw dev wlan0 link | grep -q 'Connected to' || exit 1
ping -I wlan0 -c 3 -W 2 ${AP_RADIO_IP} >/dev/null || exit 1
" >/dev/null 2>&1 || return 1

  ap_sh "
ping -I wlan0 -c 3 -W 2 ${STA_RADIO_IP} >/dev/null || exit 1
" >/dev/null 2>&1 || return 1

  return 0
}

e2e_ok() {
  log "Check extremo a extremo Spark <-> Raspi demo..."

  timeout 12 ping -c 3 -W 2 "${RASPI_DEMO_IP}" >/dev/null 2>&1 || return 1

  raspi_sh "
ping -c 3 -W 2 ${SPARK_DEMO_IP} >/dev/null || exit 1
" >/dev/null 2>&1 || return 1

  return 0
}

repair_routes() {
  log "Reparando rutas y vecinos..."

  sudo ip addr replace "${SPARK_DEMO_IP}/24" dev "${SPARK_IFACE}" 2>/dev/null || true
  sudo ip addr replace "192.168.1.170/24" dev "${SPARK_IFACE}" 2>/dev/null || true
  sudo ip route replace 172.16.13.0/24 via 172.16.12.1 dev "${SPARK_IFACE}" 2>/dev/null || true

  ap_sh "
echo 1 > /proc/sys/net/ipv4/ip_forward 2>/dev/null || true
ip route replace 172.16.13.0/24 via ${STA_RADIO_IP} dev wlan0
ip neigh flush dev wlan0 2>/dev/null || true
" >/dev/null 2>&1 || true

  sta_sh "
echo 1 > /proc/sys/net/ipv4/ip_forward 2>/dev/null || true
ip route replace 172.16.12.0/24 via ${AP_RADIO_IP} dev wlan0
ip neigh flush dev wlan0 2>/dev/null || true
" >/dev/null 2>&1 || true

  raspi_sh "
sudo ip addr replace 172.16.13.100/24 dev eth0
sudo ip addr replace 192.168.1.100/24 dev eth0
sudo ip route replace 172.16.12.0/24 via 172.16.13.1 dev eth0
" >/dev/null 2>&1 || true
}

clean_restart_radio_once() {
  local attempt="$1"

  warn "Reinicio limpio radio $attempt/${MAX_RADIO_RETRIES}"

  log "Reiniciando AP .13..."
  ap_sh '
killall -9 hostapd 2>/dev/null || true
killall -9 wpa_supplicant 2>/dev/null || true
ip link set wlan0 down 2>/dev/null || true
sleep 2
ip link set wlan0 up
sleep 2
ip addr flush dev wlan0
ip addr add 10.10.10.1/24 dev wlan0
hostapd -B /tmp/hostapd_ap13_60g.conf
sleep 4
iw dev wlan0 info || true
ip addr show dev wlan0 || true
ps w | grep hostapd | grep -v grep || true
' || return 1

  log "Reiniciando STA .12..."
  sta_sh '
killall -9 wpa_supplicant 2>/dev/null || true
killall -9 hostapd 2>/dev/null || true
rm -f /var/run/wpa_supplicant/wlan0 2>/dev/null || true
ip link set wlan0 down 2>/dev/null || true
sleep 2
ip link set wlan0 up
sleep 2
ip addr flush dev wlan0
ip addr add 10.10.10.2/24 dev wlan0
wpa_supplicant -D nl80211 -i wlan0 -c /tmp/wpa_sta12_60g.conf -B
sleep 8
iw dev wlan0 link || true
ip addr show dev wlan0 || true
' || return 1

  repair_routes
}

if [[ -z "${BASE_SETUP}" || ! -f "${BASE_SETUP}" ]]; then
  err "No encuentro setup base: ${BASE_SETUP}"
  exit 1
fi

echo
log "Ejecutando setup base: ${BASE_SETUP}"
bash "${BASE_SETUP}" || warn "El setup base devolvió error; sigo con recuperación robusta."

log "Esperando estabilización post-setup..."
sleep 5

repair_routes
sleep 2

if radio_ok; then
  log "Radio OK."

  for i in 1 2 3 4 5; do
    if e2e_ok; then
      log "Setup OK: radio y extremo a extremo funcionando."
      exit 0
    fi

    warn "Radio OK pero extremo a extremo aún falla. Reparo rutas/ARP y espero. Intento ${i}/5"
    repair_routes
    sleep 4
  done
else
  warn "Radio no OK tras setup base. Inicio reintentos limpios."
fi

for i in $(seq 1 "${MAX_RADIO_RETRIES}"); do
  clean_restart_radio_once "$i" || true

  sleep 4

  if radio_ok; then
    log "Radio OK tras reintento ${i}."
    repair_routes
    sleep 3

    for j in 1 2 3 4 5; do
      if e2e_ok; then
        log "Setup robusto OK tras reintento radio ${i}."
        exit 0
      fi
      warn "E2E falla todavía tras radio OK. Reparo rutas. Intento E2E ${j}/5"
      repair_routes
      sleep 4
    done
  else
    warn "Radio sigue sin asociar tras reintento ${i}."
  fi
done

echo
err "Setup robusto FALLÓ tras ${MAX_RADIO_RETRIES} reintentos."
echo

echo "=== Diagnóstico AP .13 ==="
ap_sh '
iw dev wlan0 info || true
ip addr show dev wlan0 || true
ip route || true
ps w | grep hostapd | grep -v grep || true
dmesg | tail -n 40 || true
' || true

echo
echo "=== Diagnóstico STA .12 ==="
sta_sh '
iw dev wlan0 link || true
ip addr show dev wlan0 || true
ip route || true
ps w | grep wpa | grep -v grep || true
dmesg | tail -n 60 || true
' || true

echo
echo "=== Diagnóstico Spark ==="
ip route get "${RASPI_DEMO_IP}" || true
ping -c 3 -W 2 "${RASPI_DEMO_IP}" || true

exit 1
