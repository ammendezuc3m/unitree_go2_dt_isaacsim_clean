#!/bin/sh
#
# Capture with countdown + rest between runs
#
# Uso:
#   ./capture_countdown.sh <label> <num_samples> <num_runs> [start_id] [rest_seconds]
#
# Ej:
#   ./capture_countdown.sh gesto1 120 100 1 10
#

LABEL="$1"
NUM_SAMPLES="$2"
NUM_RUNS="$3"
START_ID="${4:-1}"
REST_S="${5:-0}"

CAPTURE_SCRIPT="/root/scripts_csi_dog/capture_csi.sh"

if [ -z "$LABEL" ] || [ -z "$NUM_SAMPLES" ] || [ -z "$NUM_RUNS" ]; then
  echo "Usage: $0 <label> <num_samples> <num_runs> [start_id] [rest_seconds]"
  exit 1
fi

if [ ! -x "$CAPTURE_SCRIPT" ]; then
  echo "ERROR: No encuentro ejecutable $CAPTURE_SCRIPT"
  exit 1
fi

echo "======================================="
echo " Batch capture with countdown"
echo " Label       : $LABEL"
echo " Samples/TXT : $NUM_SAMPLES"
echo " Runs        : $NUM_RUNS"
echo " Start ID    : $START_ID"
echo " Rest time   : ${REST_S}s"
echo "======================================="

run_id="$START_ID"
i=0

while [ "$i" -lt "$NUM_RUNS" ]; do
  i=$((i+1))

  echo ""
  echo ">>> RUN $i/$NUM_RUNS  (run_id=$run_id) label=$LABEL"

  echo "Prepárate... empezamos en:"
  echo "3..."
  sleep 1
  echo "2..."
  sleep 1
  echo "1..."
  sleep 1
  echo "GO! 🐕"

  "$CAPTURE_SCRIPT" "$LABEL" "$NUM_SAMPLES" "$run_id"
  rc=$?

  if [ "$rc" -ne 0 ]; then
    echo "⚠️  WARNING: captura falló con código $rc en run_id=$run_id"
  else
    echo "✅ Captura completada run_id=$run_id"
  fi

  run_id=$((run_id+1))

  if [ "$REST_S" -gt 0 ] && [ "$i" -lt "$NUM_RUNS" ]; then
    echo ""
    echo "Descanso ${REST_S}s para recolocar al perro..."
    sleep "$REST_S"
  fi
done

echo ""
echo "🎉 Batch finalizado correctamente."
