#!/usr/bin/env bash
set -Eeuo pipefail

GO2_ROOT="${GO2_ROOT:-/home/nextnet/AlbertoDir/go2_dt}"
REPO_ROOT="$(cd "${GO2_ROOT}/.." && pwd)"
PROJECT_PYTHON="${REPO_ROOT}/.venv/bin/python"

CSI_LIVE_PREDICTOR="${CSI_LIVE_PREDICTOR:-${GO2_ROOT}/csi_live_predictor_from_stream.py}"
CSI_STREAM_FILE="${CSI_STREAM_FILE:-${GO2_ROOT}/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt}"
CSI_MODEL="${CSI_MODEL:-${GO2_ROOT}/csi_dog_dataset_20210421_181125/analysis_outputs/csi_empty_person_model_new_1001_1500.joblib}"
CSI_STATE_JSON="${CSI_STATE_JSON:-${GO2_ROOT}/csi_dog_dataset_20210421_181125/analysis_outputs/live_prediction_state.json}"
CSI_PREDICTIONS_CSV="${CSI_PREDICTIONS_CSV:-${GO2_ROOT}/csi_dog_dataset_20210421_181125/analysis_outputs/live_predictions_external_stream.csv}"

CSI_CONFIRM_COUNT="${CSI_CONFIRM_COUNT:-2}"
CSI_VOTE_WINDOW="${CSI_VOTE_WINDOW:-5}"

# OJO: csi_live_predictor_from_stream.py espera entero.
# 3 sobre una ventana de 5 equivale aproximadamente a 60%.
CSI_VOTE_THRESHOLD="${CSI_VOTE_THRESHOLD:-3}"

CSI_EXPECTED_TOKENS="${CSI_EXPECTED_TOKENS:-0}"
CSI_MAX_INVALID_LOG_EVERY="${CSI_MAX_INVALID_LOG_EVERY:-20}"

mkdir -p "$(dirname "${CSI_STREAM_FILE}")" "$(dirname "${CSI_STATE_JSON}")"

test -x "${PROJECT_PYTHON}" || { echo "[ERROR] Falta ${PROJECT_PYTHON}. Ejecuta: bash ${REPO_ROOT}/requirements/setup_host_env.sh" >&2; exit 1; }

echo "[csi-predictor] predictor=${CSI_LIVE_PREDICTOR}"
echo "[csi-predictor] input=${CSI_STREAM_FILE}"
echo "[csi-predictor] model=${CSI_MODEL}"
echo "[csi-predictor] state=${CSI_STATE_JSON}"
echo "[csi-predictor] csv=${CSI_PREDICTIONS_CSV}"
echo "[csi-predictor] confirm_count=${CSI_CONFIRM_COUNT}"
echo "[csi-predictor] vote_window=${CSI_VOTE_WINDOW}"
echo "[csi-predictor] vote_threshold=${CSI_VOTE_THRESHOLD}"

test -f "${CSI_LIVE_PREDICTOR}" || { echo "[ERROR] No existe ${CSI_LIVE_PREDICTOR}" >&2; exit 1; }
test -f "${CSI_MODEL}" || { echo "[ERROR] No existe ${CSI_MODEL}" >&2; exit 1; }

# Estado inicial para que Zone Patrol no lea basura.
"${PROJECT_PYTHON}" - <<PY
import json, time
p = "${CSI_STATE_JSON}"
state = {
    "source": "csi",
    "status": "starting",
    "label": "unknown",
    "predicted_class": "unknown",
    "person_detected": False,
    "updated_at": time.time()
}
with open(p, "w") as f:
    json.dump(state, f)
PY

"${PROJECT_PYTHON}" "${CSI_LIVE_PREDICTOR}" \
  --input-file "${CSI_STREAM_FILE}" \
  --model "${CSI_MODEL}" \
  --state-json "${CSI_STATE_JSON}" \
  --predictions-csv "${CSI_PREDICTIONS_CSV}" \
  --confirm-count "${CSI_CONFIRM_COUNT}" \
  --vote-window "${CSI_VOTE_WINDOW}" \
  --vote-threshold "${CSI_VOTE_THRESHOLD}" \
  --expected-tokens "${CSI_EXPECTED_TOKENS}" \
  --max-invalid-log-every "${CSI_MAX_INVALID_LOG_EVERY}"
