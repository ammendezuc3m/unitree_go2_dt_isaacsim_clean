#!/usr/bin/env bash
set -u

cd "${GO2_ROOT}"

echo "[RX-MP4-SIMPLE] esperando RTP/H264 en UDP port=6002"

gst-launch-1.0 -v \
  udpsrc port=6002 \
  caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" ! \
  rtpjitterbuffer latency=300 drop-on-latency=false ! \
  rtph264depay ! \
  h264parse ! \
  avdec_h264 ! \
  videoconvert ! \
  autovideosink sync=false
