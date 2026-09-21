#!/bin/sh
set -u

LABEL="${1:-}"
NUM_SAMPLES="${2:-}"
RUN_ID="${3:-}"

BASE_DIR="${CSI_DATASET_DIR:-/tmp/csi_dog_dataset}"
OUT_DIR="${BASE_DIR}/${LABEL}/txt"
MAC_BIN="${CSI_MAC_BIN:-/tmp/csi_peer_mac.bin}"

if [ -z "$LABEL" ] || [ -z "$NUM_SAMPLES" ] || [ -z "$RUN_ID" ]; then
  echo "Usage: $0 <label> <num_samples> <run_id>"
  exit 1
fi

mkdir -p "$OUT_DIR"
CSI_FILE="${OUT_DIR}/csi_${LABEL}_${NUM_SAMPLES}_${RUN_ID}.txt"
: > "$CSI_FILE"

echo "[CSI-CAPTURE] Output: $CSI_FILE"

# Dataset capture is self-contained: reset the STA association, then trigger CSI.
ip link set dev wlan0 down
ip link set dev wlan0 up
killall wpa_supplicant 2>/dev/null || true
wpa_supplicant -D nl80211 -i wlan0 -c "${WPA_CONFIG:-/etc/wpa_supplicant.conf}" -B

echo "[CSI-CAPTURE] Waiting for wlan0 association..."
i=0
while ! iw dev wlan0 link 2>/dev/null | grep -q "Connected to"; do
  i=$((i+1))
  if [ "$i" -ge 30 ]; then
    echo "[CSI-CAPTURE][ERROR] Cannot associate after 30 seconds"
    echo "Unreachable" > "$CSI_FILE"
    exit 1
  fi
  sleep 1
done

# The vendor CSI command expects the peer/AP MAC as six raw bytes on stdin.
# Derive it from the current association so the script is not tied to one AP MAC.
BSSID="$(iw dev wlan0 link 2>/dev/null | awk '/Connected to/ {print $3; exit}')"
if [ -z "$BSSID" ]; then
  echo "[CSI-CAPTURE][ERROR] Could not determine connected BSSID"
  exit 1
fi
ESCAPED="$(echo "$BSSID" | awk -F: '{printf "\\x%s\\x%s\\x%s\\x%s\\x%s\\x%s", $1,$2,$3,$4,$5,$6}')"
printf '%b' "$ESCAPED" > "$MAC_BIN"

if [ "$(wc -c < "$MAC_BIN")" -ne 6 ]; then
  echo "[CSI-CAPTURE][ERROR] Invalid peer MAC binary: $BSSID"
  exit 1
fi

echo "[CSI-CAPTURE] Peer BSSID: $BSSID"
dmesg -c >/dev/null 2>&1 || true

SAMPLES_DONE=0
while [ "$SAMPLES_DONE" -lt "$NUM_SAMPLES" ]; do
  if ! iw dev wlan0 link 2>/dev/null | grep -q "Connected to"; then
    echo "[CSI-CAPTURE][WARN] Lost association; stopping"
    break
  fi

  # Trigger CSI on the MikroTik/OpenWrt device itself.
  cat "$MAC_BIN" | iw dev wlan0 vendor recv 0x001374 0x93 - >/dev/null 2>&1
  sleep "${CSI_TRIGGER_DELAY_S:-0.02}"

  LAST_LINE="$(dmesg | grep "\[AOA\] Measurement:" | tail -n 1)"
  echo "$LAST_LINE" | grep -q "Measurement:" || continue

  echo "$LAST_LINE" >> "$CSI_FILE"
  SAMPLES_DONE=$((SAMPLES_DONE+1))
  echo "[CSI-CAPTURE] $SAMPLES_DONE / $NUM_SAMPLES"
done

killall wpa_supplicant 2>/dev/null || true

echo "[CSI-CAPTURE] Captured $SAMPLES_DONE samples"
echo "[CSI-CAPTURE] Saved to $CSI_FILE"
