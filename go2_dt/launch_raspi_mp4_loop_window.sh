#!/usr/bin/env bash
set -Eeuo pipefail

RASPI_USER="${RASPI_USER:-nextnet}"

RASPI_HOST="${RASPI_HOST:-172.16.13.100}"
SPARK_IP="${SPARK_IP:-172.16.12.170}"
MP4_FILE="${MP4_FILE:-/home/${RASPI_USER}/raspi_60ghz_demo/golden_test.mp4}"
MP4_PORT="${MP4_PORT:-6002}"

echo "[R6-LAUNCHER] Raspi=${RASPI_HOST}"
echo "[R6-LAUNCHER] Spark=${SPARK_IP}"
echo "[R6-LAUNCHER] MP4=${MP4_FILE}"
echo "[R6-LAUNCHER] Port=${MP4_PORT}"

REMOTE_CMD=$(cat <<EOF
set -u

echo '[R6] limpiando solo gst filesrc anterior...'
pkill -9 -f 'gst-launch-1.0.*filesrc' 2>/dev/null || true

echo '[R6] comprobando script MP4 loop...'
ls -l /home/system/raspi_60ghz_demo/mp4_loop_tx_6002.sh || exit 1

echo '[R6] arrancando loop MP4...'
SPARK_IP='${SPARK_IP}' \\
MP4_FILE='${MP4_FILE}' \\
MP4_PORT='${MP4_PORT}' \\
MP4_WIDTH='1280' \\
MP4_HEIGHT='720' \\
MP4_FPS='15' \\
MP4_BITRATE_KBPS='20000' \\
/home/system/raspi_60ghz_demo/mp4_loop_tx_6002.sh
EOF
)

if command -v gnome-terminal >/dev/null 2>&1; then
  gnome-terminal --title="R6 - Raspi MP4 Loop TX" -- bash -lc "
    ssh -tt -o ConnectTimeout=8 nextnet@'${RASPI_HOST}' $(printf "%q" "$REMOTE_CMD")
    echo
    echo '[R6 - Raspi MP4 Loop TX] proceso terminado. Pulsa ENTER para cerrar...'
    read -r
  " &
elif command -v xterm >/dev/null 2>&1; then
  xterm -T "R6 - Raspi MP4 Loop TX" -e bash -lc "
    ssh -tt -o ConnectTimeout=8 nextnet@'${RASPI_HOST}' $(printf "%q" "$REMOTE_CMD")
    echo
    echo '[R6 - Raspi MP4 Loop TX] proceso terminado. Pulsa ENTER para cerrar...'
    read -r
  " &
else
  echo "[R6-LAUNCHER][ERROR] No encuentro gnome-terminal ni xterm."
  exit 1
fi
