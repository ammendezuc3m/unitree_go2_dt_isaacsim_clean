#!/usr/bin/env bash
set -Eeuo pipefail

SPARK_IP="${SPARK_IP:-172.16.12.170}"
IPERF_PORT="${IPERF_PORT:-5201}"
IPERF_BITRATE="${IPERF_BITRATE:-400M}"
IPERF_MODE="${IPERF_MODE:-tcp}"
IPERF_DURATION="${IPERF_DURATION:-3600}"
IPERF_UDP_LENGTH="${IPERF_UDP_LENGTH:-1200}"

echo "[iperf-loop] target=${SPARK_IP}:${IPERF_PORT}"
echo "[iperf-loop] mode=${IPERF_MODE}, bitrate=${IPERF_BITRATE}, duration=${IPERF_DURATION}"

while true; do
  if [[ "${IPERF_MODE}" == "tcp" ]]; then
    iperf3 -c "${SPARK_IP}" -p "${IPERF_PORT}" -b "${IPERF_BITRATE}" -t "${IPERF_DURATION}" -i 1
  else
    iperf3 -u -c "${SPARK_IP}" -p "${IPERF_PORT}" -b "${IPERF_BITRATE}" -l "${IPERF_UDP_LENGTH}" -t "${IPERF_DURATION}" -i 1
  fi

  rc=$?
  echo "[iperf-loop] iperf3 ended rc=${rc}; restarting in 2s"
  sleep 2
done
