#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-${REPO_ROOT}/.venv}"
ROS_DISTRO="${ROS_DISTRO:-jazzy}"
ROS_SETUP="/opt/ros/${ROS_DISTRO}/setup.bash"
REQ_FILE="${REPO_ROOT}/requirements/requirements-host-all.txt"

log() { echo "[host-env] $*"; }
err() { echo "[host-env][ERROR] $*" >&2; }

if [[ ! -f "${ROS_SETUP}" ]]; then
  err "ROS 2 ${ROS_DISTRO} no está instalado en ${ROS_SETUP}."
  err "Instala ROS 2 Jazzy antes de crear el entorno Python del proyecto."
  exit 1
fi

if [[ ! -f "${REQ_FILE}" ]]; then
  err "No existe ${REQ_FILE}"
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  err "git no está instalado."
  exit 1
fi

if ! command -v git-lfs >/dev/null 2>&1 && ! git lfs version >/dev/null 2>&1; then
  err "Git LFS no está instalado."
  err "Instálalo con: sudo apt install git-lfs"
  exit 1
fi

log "Inicializando Git LFS y descargando assets..."
cd "${REPO_ROOT}"
git lfs install --local
git lfs pull

bash "${REPO_ROOT}/requirements/check_lfs_assets.sh"

log "Creando/actualizando entorno: ${VENV_DIR}"
python3 -m venv --system-site-packages "${VENV_DIR}"

# ROS Python packages (rclpy, cv_bridge, tf2_ros, etc.) come from apt.
# --system-site-packages makes them visible inside the venv.
source "${ROS_SETUP}"
source "${VENV_DIR}/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r "${REQ_FILE}"

log "Validando imports principales..."
python - <<'PY'
import importlib

modules = [
    "numpy",
    "scipy",
    "pandas",
    "matplotlib",
    "cv2",
    "rclpy",
    "tf2_ros",
    "cv_bridge",
    "aiortc",
    "aiohttp",
    "requests",
    "Crypto",
    "cryptography",
    "wasmtime",
    "sklearn",
    "joblib",
    "torch",
    "torchvision",
    "loguru",
]

failed = []
for name in modules:
    try:
        importlib.import_module(name)
        print(f"[OK] {name}")
    except Exception as exc:
        failed.append((name, repr(exc)))
        print(f"[FAIL] {name}: {exc}")

import cv2
if not hasattr(cv2, "aruco"):
    failed.append(("cv2.aruco", "OpenCV contrib module unavailable"))
    print("[FAIL] cv2.aruco: OpenCV contrib module unavailable")
else:
    print("[OK] cv2.aruco")

if failed:
    print("\nEnvironment validation failed:")
    for name, exc in failed:
        print(f"  - {name}: {exc}")
    raise SystemExit(1)

print("\nUnified host environment is ready.")
PY

log "Hecho."
log "Para usarlo:"
log "  source ${ROS_SETUP}"
log "  source ${VENV_DIR}/bin/activate"
