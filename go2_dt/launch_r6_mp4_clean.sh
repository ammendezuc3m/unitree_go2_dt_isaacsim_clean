#!/usr/bin/env bash
set -Eeuo pipefail

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
SPARK_IP="${SPARK_IP:-172.16.12.170}"
MP4_FILE="${MP4_FILE:-/home/nextnet/raspi_60ghz_demo/golden_test.mp4}"
MP4_PORT="${MP4_PORT:-6002}"

echo "[R6-SIMPLE] start"
echo "[R6-SIMPLE] Raspi=${RASPI_HOST}"
echo "[R6-SIMPLE] Spark=${SPARK_IP}"
echo "[R6-SIMPLE] MP4=${MP4_FILE}"
echo "[R6-SIMPLE] Port=${MP4_PORT}"

ssh_try() {
  local host="$1"
  shift
  ssh -o ConnectTimeout=8 nextnet@"$host" "$@"
}

echo "[R6-SIMPLE] limpiando TX viejos..."
ssh_try "${RASPI_HOST}" '
pkill -9 -f "mp4_loop_tx_6002.sh" 2>/dev/null || true
pkill -9 -f "tx_mp4_simple_loop_6002.sh" 2>/dev/null || true
pkill -9 -f "gst-launch-1.0.*filesrc" 2>/dev/null || true
' || ssh_try "${RASPI_MGMT_HOST}" '
pkill -9 -f "mp4_loop_tx_6002.sh" 2>/dev/null || true
pkill -9 -f "tx_mp4_simple_loop_6002.sh" 2>/dev/null || true
pkill -9 -f "gst-launch-1.0.*filesrc" 2>/dev/null || true
'

echo "[R6-SIMPLE] lanzando TX simple en primer plano..."
ssh_try "${RASPI_HOST}" "
SPARK_IP='${SPARK_IP}' \
PORT='${MP4_PORT}' \
MP4_FILE='${MP4_FILE}' \
/home/nextnet/raspi_60ghz_demo/tx_mp4_simple_loop_6002.sh
"
