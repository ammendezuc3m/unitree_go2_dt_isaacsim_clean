#!/usr/bin/env bash
set -u

SPARK_IP="${SPARK_IP:-172.16.12.170}"
MP4_PORT="${MP4_PORT:-6002}"

# Reinicio periódico del RX.
# Debe ser algo mayor que la duración del MP4 si quieres evitar cortes antes del final.
# Si no sabes la duración, 55s suele recuperar rápido cuando se queda negro.
MP4_RX_RESTART_SEC="${MP4_RX_RESTART_SEC:-220}"

echo "[MP4-RX-LOOP] start"
echo "[MP4-RX-LOOP] listen=${SPARK_IP}:${MP4_PORT}"
echo "[MP4-RX-LOOP] restart cada ${MP4_RX_RESTART_SEC}s"

while true; do
  echo "[MP4-RX-LOOP] launching gst receiver..."

  timeout --foreground "${MP4_RX_RESTART_SEC}" \
  gst-launch-1.0 -v \
    udpsrc address="${SPARK_IP}" port="${MP4_PORT}" \
      caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" ! \
    rtpjitterbuffer latency=100 drop-on-latency=true ! \
    rtph264depay ! h264parse ! avdec_h264 ! \
    videoconvert ! autovideosink sync=false

  rc=$?
  echo "[MP4-RX-LOOP] gst receiver ended rc=${rc}; restarting in 0.5s"
  sleep 0.5
done
