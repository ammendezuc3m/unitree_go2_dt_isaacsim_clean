#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GO2_ROOT="${GO2_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"

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

STA12_HOST="${STA12_HOST:-192.168.1.12}"
STA12_USER="${STA12_USER:-root}"

CSI_REMOTE_SCRIPT="${CSI_REMOTE_SCRIPT:-/root/scripts_csi_dog/stream_csi_stdout_05s_ap13.sh}"
CSI_STREAM_FILE="${CSI_STREAM_FILE:-${GO2_ROOT}/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt}"

LOG_DIR="${GO2_ROOT}/tests_experiments/tmp_outputs/raspi_60ghz_ap13_sta12"
LOG_FILE="${LOG_DIR}/csi_sta12_stream.log"

mkdir -p "$(dirname "${CSI_STREAM_FILE}")"
mkdir -p "${LOG_DIR}"

: > "${CSI_STREAM_FILE}"
: > "${LOG_FILE}"

echo "[csi-sta12] mode=Spark -> Raspi WiFi -> STA12" | tee -a "${LOG_FILE}"
echo "[csi-sta12] RASPI_MGMT_HOST=${RASPI_MGMT_HOST}" | tee -a "${LOG_FILE}"
echo "[csi-sta12] STA12_HOST=${STA12_HOST}" | tee -a "${LOG_FILE}"
echo "[csi-sta12] CSI_STREAM_FILE=${CSI_STREAM_FILE}" | tee -a "${LOG_FILE}"
echo "[csi-sta12] CSI_REMOTE_SCRIPT=${CSI_REMOTE_SCRIPT}" | tee -a "${LOG_FILE}"

ssh -oStrictHostKeyChecking=no \
    -oUserKnownHostsFile=/dev/null \
    -oHostKeyAlgorithms=+ssh-rsa \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    "${RASPI_USER}@${RASPI_MGMT_HOST}" \
    "ssh -oStrictHostKeyChecking=no \
         -oUserKnownHostsFile=/dev/null \
         -oHostKeyAlgorithms=+ssh-rsa \
         -oPubkeyAcceptedAlgorithms=+ssh-rsa \
         ${STA12_USER}@${STA12_HOST} '
echo [CSI-STA12] limpiando streamers antiguos...
for pid in $(ps w | grep -Ei \"stream_csi_stdout|stream_csi|iw dev wlan0 vendor|vendor recv|csi_stdout\" | grep -v grep | awk \"{print \\\$1}\"); do
  echo kill CSI pid=$pid
  kill -9 $pid 2>/dev/null || true
done
rm -f /tmp/csi_stdout_05s_ap13.lock 2>/dev/null || true
echo [CSI-STA12] arrancando streamer...
'${CSI_REMOTE_SCRIPT}'
'" \
  2>&1 | tee -a "${LOG_FILE}" | tee -a "${CSI_STREAM_FILE}"
