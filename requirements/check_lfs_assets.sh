#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ASSETS=(
  "go2_dt/camera_yolo/Weights/yolox_s.pth"
  "go2_dt/csi_dog_dataset_20210421_181125/analysis_outputs/csi_empty_person_model_20260526_all_1_2200.joblib"
  "go2_dt/media/golden_test.mp4"
  "go2_assets/go2/prueba_demo2_new_scenario.usd"
)

failed=0

for rel in "${ASSETS[@]}"; do
  path="${REPO_ROOT}/${rel}"

  if [[ ! -f "${path}" ]]; then
    echo "[LFS][FAIL] Missing required asset: ${rel}" >&2
    failed=1
    continue
  fi

  if head -n 1 "${path}" 2>/dev/null | grep -q "^version https://git-lfs.github.com/spec/v1"; then
    echo "[LFS][FAIL] Asset is still a Git LFS pointer: ${rel}" >&2
    failed=1
    continue
  fi

  size="$(stat -c %s "${path}" 2>/dev/null || wc -c < "${path}")"
  echo "[LFS][OK] ${rel} (${size} bytes)"
done

if [[ "${failed}" -ne 0 ]]; then
  echo >&2
  echo "[LFS][ERROR] Required runtime assets are not materialized." >&2
  echo "[LFS][ERROR] From the repository root run:" >&2
  echo "  git lfs install" >&2
  echo "  git lfs pull" >&2
  exit 1
fi

echo "[LFS] All required runtime assets are present."
