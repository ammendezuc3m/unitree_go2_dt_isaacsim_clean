#!/usr/bin/env bash
set -Eeuo pipefail

SPARK_IP="${SPARK_IP:-172.16.12.170}"
MP4_FILE="${MP4_FILE:-${HOME}/raspi_60ghz_demo/golden_test.mp4}"
MP4_PORT="${MP4_PORT:-6002}"

MP4_WIDTH="${MP4_WIDTH:-1280}"
MP4_HEIGHT="${MP4_HEIGHT:-720}"
MP4_FPS="${MP4_FPS:-15}"
MP4_BITRATE_KBPS="${MP4_BITRATE_KBPS:-20000}"
RTP_MTU="${RTP_MTU:-1200}"

if [[ ! -f "${MP4_FILE}" ]]; then
  echo "[MP4-LOOP][ERROR] MP4 file not found: ${MP4_FILE}" >&2
  exit 1
fi

echo "[MP4-LOOP] file=${MP4_FILE}"
echo "[MP4-LOOP] dst=${SPARK_IP}:${MP4_PORT}"

while true; do
  date '+[MP4-LOOP] %F %T launching GStreamer'

  set +e
  gst-launch-1.0 -q \
    filesrc location="${MP4_FILE}" ! \
    qtdemux name=demux \
    demux.video_0 ! queue ! decodebin ! \
    videoconvert ! videoscale ! videorate ! \
    video/x-raw,width="${MP4_WIDTH}",height="${MP4_HEIGHT}",framerate="${MP4_FPS}/1" ! \
    identity sync=true ! \
    queue max-size-buffers=8 leaky=downstream ! \
    x264enc tune=zerolatency pass=cbr bitrate="${MP4_BITRATE_KBPS}" speed-preset=ultrafast key-int-max="${MP4_FPS}" bframes=0 byte-stream=true sliced-threads=true vbv-buf-capacity=1000 option-string="nal-hrd=cbr:force-cfr=1" ! \
    h264parse config-interval=1 ! \
    rtph264pay config-interval=1 pt=96 mtu="${RTP_MTU}" ! \
    udpsink host="${SPARK_IP}" port="${MP4_PORT}" sync=false async=false
  rc=$?
  set -e

  date "+[MP4-LOOP] %F %T GStreamer ended rc=${rc}; restarting in 0.5s"
  sleep 0.5
done
