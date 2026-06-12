#!/usr/bin/env bash
set -Eeuo pipefail

SPARK_IP="${SPARK_IP:-172.16.12.170}"

CAMERA_DEVICE="${CAMERA_DEVICE:-/dev/video0}"
CAMERA_WIDTH="${CAMERA_WIDTH:-424}"
CAMERA_HEIGHT="${CAMERA_HEIGHT:-240}"
CAMERA_FPS="${CAMERA_FPS:-15}"
CAMERA_BITRATE_KBPS="${CAMERA_BITRATE_KBPS:-2000}"
CAMERA_PORT="${CAMERA_PORT:-6000}"

MP4_FILE="${MP4_FILE:-/home/nextnet/raspi_60ghz_demo/golden_test.mp4}"
USE_SAFE_MP4_LOOP="${USE_SAFE_MP4_LOOP:-1}"
MP4_WIDTH="${MP4_WIDTH:-1280}"
MP4_HEIGHT="${MP4_HEIGHT:-720}"
MP4_FPS="${MP4_FPS:-15}"
MP4_BITRATE_KBPS="${MP4_BITRATE_KBPS:-20000}"
MP4_PORT="${MP4_PORT:-6002}"
ENABLE_MP4="${ENABLE_MP4:-0}"
ENABLE_CAMERA="${ENABLE_CAMERA:-1}"
ENABLE_IPERF="${ENABLE_IPERF:-1}"

IPERF_PORT="${IPERF_PORT:-5201}"
IPERF_BITRATE="${IPERF_BITRATE:-400M}"
IPERF_MODE="${IPERF_MODE:-tcp}"
IPERF_DURATION="${IPERF_DURATION:-3600}"
IPERF_UDP_LENGTH="${IPERF_UDP_LENGTH:-1200}"

LOG_DIR="/home/nextnet/raspi_60ghz_demo/logs"
mkdir -p "$LOG_DIR"

echo "[raspi-tx] Cleaning old processes..."
pkill -9 -f "gst-launch-1.0.*udpsink.*${CAMERA_PORT}" 2>/dev/null || true
pkill -9 -f "gst-launch-1.0.*udpsink.*${MP4_PORT}" 2>/dev/null || true
pkill -9 -f "iperf3.*${SPARK_IP}" 2>/dev/null || true

echo "[raspi-tx] Target Spark: ${SPARK_IP}"
echo "[raspi-tx] Camera: enable=${ENABLE_CAMERA}, ${CAMERA_DEVICE} ${CAMERA_WIDTH}x${CAMERA_HEIGHT}@${CAMERA_FPS}, ${CAMERA_BITRATE_KBPS} kbps -> UDP/RTP ${CAMERA_PORT}"
echo "[raspi-tx] MP4: enable=${ENABLE_MP4}, ${MP4_FILE}, ${MP4_BITRATE_KBPS} kbps -> UDP/RTP ${MP4_PORT}"
echo "[raspi-tx] iperf: enable=${ENABLE_IPERF}, mode=${IPERF_MODE}, bitrate=${IPERF_BITRATE}, port=${IPERF_PORT}"

# Persistent iperf loop.
if [[ "${ENABLE_IPERF:-1}" == "1" ]]; then
  echo "[raspi-tx] launching persistent iperf loop"
  pkill -9 -f "iperf_client_loop.sh" 2>/dev/null || true
  pkill -9 -f "iperf3.*${SPARK_IP}" 2>/dev/null || true

  if [[ -x /home/system/raspi_60ghz_demo/iperf_client_loop.sh ]]; then
    SPARK_IP="${SPARK_IP}" \
    IPERF_PORT="${IPERF_PORT}" \
    IPERF_BITRATE="${IPERF_BITRATE}" \
    IPERF_MODE="${IPERF_MODE}" \
    IPERF_DURATION="${IPERF_DURATION}" \
    IPERF_UDP_LENGTH="${IPERF_UDP_LENGTH}" \
    /home/system/raspi_60ghz_demo/iperf_client_loop.sh \
      > "${LOG_DIR}/iperf_client.log" 2>&1 &

    echo $! > "${LOG_DIR}/iperf_client.pid"

    # Avoid launching the legacy iperf block too.
    ENABLE_IPERF=0
  else
    echo "[raspi-tx][WARN] /home/system/raspi_60ghz_demo/iperf_client_loop.sh not found; using direct iperf3 block"
  fi
fi

if [[ "${ENABLE_CAMERA}" == "1" ]]; then
  echo "[raspi-tx] Starting camera stream..."

  gst-launch-1.0 -v \
    v4l2src device="${CAMERA_DEVICE}" do-timestamp=true ! \
    image/jpeg,width=640,height=480,framerate=${CAMERA_FPS}/1 ! \
    jpegdec ! videoconvert ! videoscale ! videorate ! \
    video/x-raw,format=I420,width=${CAMERA_WIDTH},height=${CAMERA_HEIGHT},framerate=${CAMERA_FPS}/1 ! \
    x264enc tune=zerolatency speed-preset=ultrafast bitrate=${CAMERA_BITRATE_KBPS} key-int-max=${CAMERA_FPS} bframes=0 byte-stream=true ! \
    h264parse config-interval=1 ! \
    rtph264pay config-interval=1 pt=96 mtu=1200 ! \
    udpsink host="${SPARK_IP}" port="${CAMERA_PORT}" sync=false async=false \
    > "${LOG_DIR}/camera_tx.log" 2>&1 &

  echo $! > "${LOG_DIR}/camera_tx.pid"
fi

if [[ "${ENABLE_MP4}" == "1" ]]; then
  if [[ ! -f "${MP4_FILE}" ]]; then
    echo "[raspi-tx][ERROR] MP4 no existe: ${MP4_FILE}" | tee "${LOG_DIR}/mp4_tx.log"
  else
    echo "[raspi-tx] Starting MP4 stream..."

    gst-launch-1.0 -v \
      filesrc location="${MP4_FILE}" ! \
      qtdemux name=demux \
      demux.video_0 ! queue ! h264parse ! avdec_h264 ! \
      videoconvert ! videoscale ! videorate ! \
      video/x-raw,format=I420,width=${MP4_WIDTH},height=${MP4_HEIGHT},framerate=${MP4_FPS}/1 ! \
      identity sync=true ! \
      queue max-size-buffers=8 leaky=downstream ! \
      x264enc tune=zerolatency pass=cbr bitrate=${MP4_BITRATE_KBPS} speed-preset=ultrafast key-int-max=${MP4_FPS} bframes=0 byte-stream=true sliced-threads=true vbv-buf-capacity=1000 option-string="nal-hrd=cbr:force-cfr=1" ! \
      h264parse config-interval=1 ! \
      rtph264pay config-interval=1 pt=96 mtu=1200 ! \
      udpsink host="${SPARK_IP}" port="${MP4_PORT}" sync=false async=false \
      > "${LOG_DIR}/mp4_tx.log" 2>&1 &

    echo $! > "${LOG_DIR}/mp4_tx.pid"
  fi
fi

if [[ "${ENABLE_IPERF}" == "1" ]]; then
  echo "[raspi-tx] Starting iperf client..."

  if [[ "${IPERF_MODE}" == "tcp" ]]; then
    iperf3 -c "${SPARK_IP}" -p "${IPERF_PORT}" -b "${IPERF_BITRATE}" -t "${IPERF_DURATION}" -i 1 \
      > "${LOG_DIR}/iperf_client.log" 2>&1 &
  else
    iperf3 -u -c "${SPARK_IP}" -p "${IPERF_PORT}" -b "${IPERF_BITRATE}" -l "${IPERF_UDP_LENGTH}" -t "${IPERF_DURATION}" -i 1 \
      > "${LOG_DIR}/iperf_client.log" 2>&1 &
  fi

  echo $! > "${LOG_DIR}/iperf_client.pid"
fi

echo "[raspi-tx] Started."
echo "[raspi-tx] PIDs:"
cat "${LOG_DIR}"/*.pid 2>/dev/null || true
