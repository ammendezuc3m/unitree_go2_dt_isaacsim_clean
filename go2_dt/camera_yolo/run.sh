#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}/YOLOX:${PYTHONPATH:-}"

"${SCRIPT_DIR}/yolox/bin/python" "${SCRIPT_DIR}/YOLOX/tools/realsense_right_half_demo.py" \
  -f "${SCRIPT_DIR}/YOLOX/exps/default/yolox_s.py" \
  -c "${SCRIPT_DIR}/Weights/yolox_s.pth" \
  --device gpu --conf 0.25 --nms 0.45 --tsize 640 \
  --frame-timeout-ms 8000 --max-frame-retries 3 \
  --json-out "${SCRIPT_DIR}/outputs/live_camera_state.json"
