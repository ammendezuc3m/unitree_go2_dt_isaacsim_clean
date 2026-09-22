#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$ROOT"

OUT_DIR="$ROOT/_cleanup_reports_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT_DIR"

ENTRYPOINTS=(
  "run_full_demo_current.sh"
  "stop_full_demo_current.sh"
  "demo_60ghz_video_iperf_csi_yolo_usb.sh"
  "run_full_demo_ap13_sta12_raspi.sh"
  "stop_full_demo_ap13_sta12_raspi_clean.sh"
  "tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh"
  "tests_experiments/raspi_60ghz_ap13_sta12/run_demo_ap13_sta12_no_setup.sh"
  "tests_experiments/raspi_60ghz_ap13_sta12/stop_ap13_sta12_pipeline.sh"
)

KEEP_ROOTS=(
  "camera_yolo"
  "csi_dog_dataset_20210421_181125"
  "media"
  "ros2_ws"
  "tests_experiments"
  "venvs"
  "debug_logs"
)

KEEP_FILES=(
  "run_full_demo_current.sh"
  "stop_full_demo_current.sh"
  "demo_60ghz_video_iperf_csi_yolo_usb.sh"
  "run_full_demo_ap13_sta12_raspi.sh"
  "stop_full_demo_ap13_sta12_raspi_clean.sh"
  "csi_live_predictor_from_stream.py"
  "video_file_tx_rx_yolo_usb.py"
  "throughput_live_tk.py"
  "README.md"
  "README_RASPI.md"
)

SEARCH_EXCLUDES=(
  --exclude-dir=".git"
  --exclude-dir="_cleanup_junk_*"
  --exclude-dir="_quarantine_*"
  --exclude-dir="debug_logs"
  --exclude-dir="build"
  --exclude-dir="install"
  --exclude-dir="log"
  --exclude-dir="__pycache__"
  --exclude="*.pyc"
  --exclude="*.log"
)

echo "=== ENTRYPOINTS ===" | tee "$OUT_DIR/entrypoints.txt"
printf "%s\n" "${ENTRYPOINTS[@]}" | tee -a "$OUT_DIR/entrypoints.txt"

echo "=== KEEP ROOTS ===" | tee "$OUT_DIR/keep_roots.txt"
printf "%s\n" "${KEEP_ROOTS[@]}" | tee -a "$OUT_DIR/keep_roots.txt"

echo "=== KEEP FILES ===" | tee "$OUT_DIR/keep_files.txt"
printf "%s\n" "${KEEP_FILES[@]}" | tee -a "$OUT_DIR/keep_files.txt"

echo
echo "=== Preflight existence ===" | tee "$OUT_DIR/preflight.txt"
for f in "${ENTRYPOINTS[@]}" "${KEEP_FILES[@]}"; do
  if [ -e "$f" ]; then
    echo "OK $f" | tee -a "$OUT_DIR/preflight.txt"
  else
    echo "MISSING $f" | tee -a "$OUT_DIR/preflight.txt"
  fi
done

echo
echo "=== Root candidates analysis ==="
: > "$OUT_DIR/root_candidates_unused.txt"
: > "$OUT_DIR/root_candidates_referenced.txt"
: > "$OUT_DIR/root_candidates_keep.txt"

for item in ./*; do
  name="${item#./}"

  # Ignorar reports nuevos.
  [[ "$name" == _cleanup_reports_* ]] && continue

  keep=0

  for k in "${KEEP_ROOTS[@]}"; do
    [[ "$name" == "$k" ]] && keep=1
  done

  for k in "${KEEP_FILES[@]}"; do
    [[ "$name" == "$k" ]] && keep=1
  done

  if [[ "$keep" -eq 1 ]]; then
    echo "$name" >> "$OUT_DIR/root_candidates_keep.txt"
    continue
  fi

  base="$(basename "$name")"

  if grep -RInF "${SEARCH_EXCLUDES[@]}" -- "$base" . >/tmp/audit_refs_$$ 2>/dev/null; then
    {
      echo "### $name"
      cat /tmp/audit_refs_$$
      echo
    } >> "$OUT_DIR/root_candidates_referenced.txt"
  else
    echo "$name" >> "$OUT_DIR/root_candidates_unused.txt"
  fi

  rm -f /tmp/audit_refs_$$
done

echo
echo "[OK] Reporte creado en: $OUT_DIR"
echo
echo "Revisar:"
echo "  $OUT_DIR/root_candidates_keep.txt"
echo "  $OUT_DIR/root_candidates_referenced.txt"
echo "  $OUT_DIR/root_candidates_unused.txt"
