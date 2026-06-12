#!/usr/bin/env bash
set -Eeuo pipefail

GO2_ROOT="${GO2_ROOT:-/home/nextnet/AlbertoDir/go2_dt}"

export RASPI_HOST="${RASPI_HOST:-172.16.13.100}"
export RASPI_MGMT_HOST="${RASPI_MGMT_HOST:-192.168.1.100}"
export SPARK_IP="${SPARK_IP:-172.16.12.170}"
export STA_MGMT_HOST="${STA_MGMT_HOST:-192.168.1.12}"

export IPERF_MODE="${IPERF_MODE:-tcp}"
export IPERF_BITRATE="${IPERF_BITRATE:-70M}"

export ENABLE_CAMERA="${ENABLE_CAMERA:-1}"
export ENABLE_MP4="${ENABLE_MP4:-1}"
export ENABLE_IPERF="${ENABLE_IPERF:-1}"
export ENABLE_CSI="${ENABLE_CSI:-0}"

# Desactivamos la gráfica antigua porque consultaba radio/remoto.
export ENABLE_THROUGHPUT=0

export THROUGHPUT_SCALE_MAX="${THROUGHPUT_SCALE_MAX:-140}"
export YOLO_TSIZE="${YOLO_TSIZE:-160}"
export YOLO_PROCESS_FPS="${YOLO_PROCESS_FPS:-1}"

cd "${GO2_ROOT}"

echo "[run-ap13-sta12] RASPI_HOST=${RASPI_HOST}"
echo "[run-ap13-sta12] SPARK_IP=${SPARK_IP}"
echo "[run-ap13-sta12] STA_MGMT_HOST=${STA_MGMT_HOST}"
echo "[run-ap13-sta12] IPERF_BITRATE=${IPERF_BITRATE}"
echo "[run-ap13-sta12] Throughput plot: LOCAL RX ONLY on enP7s7"

pkill -9 -f "throughput_local_rx_tk.py" 2>/dev/null || true

gnome-terminal --title="R4 - 60GHz Local RX Throughput" -- bash -lc "
cd '${GO2_ROOT}'
python3 tests_experiments/raspi_60ghz_ap13_sta12/throughput_local_rx_tk.py \
  --iface enP7s7 \
  --scale-max '${THROUGHPUT_SCALE_MAX}' \
  --window 240 \
  --refresh 1.0 \
  --smooth-samples 3
exec bash
" 2>/dev/null || \
xterm -T "R4 - 60GHz Local RX Throughput" -e "
cd '${GO2_ROOT}'
python3 tests_experiments/raspi_60ghz_ap13_sta12/throughput_local_rx_tk.py \
  --iface enP7s7 \
  --scale-max '${THROUGHPUT_SCALE_MAX}' \
  --window 240 \
  --refresh 1.0 \
  --smooth-samples 3
bash
" 2>/dev/null || true

./tests_experiments/raspi_60ghz/run_raspi_60ghz_pipeline.sh
