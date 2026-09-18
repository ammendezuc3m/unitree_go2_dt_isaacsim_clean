#!/usr/bin/env bash
set -Eeuo pipefail

SPARK_IP="${SPARK_IP:-172.16.12.170}"
MP4_PORT="${MP4_PORT:-6002}"

echo "[MP4-RX-STABLE] start"
echo "[MP4-RX-STABLE] listen=${SPARK_IP}:${MP4_PORT}"
echo "[MP4-RX-STABLE] ventana estable, sin timeout/restart"

gst-launch-1.0 -v \
  udpsrc address="${SPARK_IP}" port="${MP4_PORT}" \
    caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" ! \
  rtpjitterbuffer latency=300 drop-on-latency=true ! \
  rtph264depay ! h264parse ! avdec_h264 ! \
  videoconvert ! autovideosink sync=false
