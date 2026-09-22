#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GO2_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${GO2_ROOT}/.." && pwd)"
PROJECT_PYTHON="${REPO_ROOT}/.venv/bin/python"

if [[ ! -x "${PROJECT_PYTHON}" ]]; then
  echo "[camera-yolo][ERROR] Missing ${PROJECT_PYTHON}. Run: bash ${REPO_ROOT}/requirements/setup_host_env.sh" >&2
  exit 1
fi

export PYTHONPATH="${SCRIPT_DIR}/YOLOX:${PYTHONPATH:-}"

"${PROJECT_PYTHON}" "${SCRIPT_DIR}/YOLOX/tools/realsense_right_half_demo.py" \
  -f "${SCRIPT_DIR}/YOLOX/exps/default/yolox_s.py" \
  -c "${SCRIPT_DIR}/Weights/yolox_s.pth" \
  --device gpu --conf 0.25 --nms 0.45 --tsize 640 \
  --frame-timeout-ms 8000 --max-frame-retries 3 \
  --json-out "${SCRIPT_DIR}/outputs/live_camera_state.json"
