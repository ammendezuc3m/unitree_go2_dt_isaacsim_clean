#!/bin/sh

LABEL="$1"
NUM_SAMPLES="$2"
RUN_ID="$3"

BASE_DIR="/tmp/csi_dog_dataset"
OUT_DIR="${BASE_DIR}/${LABEL}/txt"

if [ -z "$LABEL" ] || [ -z "$NUM_SAMPLES" ] || [ -z "$RUN_ID" ]; then
  echo "Usage: $0 <label> <num_samples> <run_id>"
  exit 1
fi

mkdir -p "$OUT_DIR"

CSI_FILE="${OUT_DIR}/csi_${LABEL}_${NUM_SAMPLES}_${RUN_ID}.txt"
echo "Output file: $CSI_FILE"
: > "$CSI_FILE"

# Restart Wi-Fi and reconnect the STA.
ip link set dev wlan0 down
ip link set dev wlan0 up

killall wpa_supplicant 2>/dev/null
wpa_supplicant -D nl80211 -i wlan0 -c /etc/wpa_supplicant.conf -B

echo "Connecting..."
i=0
while true; do
  connected=$(cat /sys/kernel/debug/ieee80211/phy0/wil6210/stations 2>/dev/null | grep -c connected)
  if [ "$connected" -eq 1 ]; then
    echo "We are connected now"
    break
  fi

  i=$((i+1))
  if [ "$i" -ge 30 ]; then
    echo "We cannot connect after 30 seconds"
    echo "Unreachable" > "$CSI_FILE"
    exit 1
  fi
  sleep 1
done

SAMPLES_DONE=0

# Drop old kernel messages once before starting the capture.
dmesg -c >/dev/null 2>&1

while [ "$SAMPLES_DONE" -lt "$NUM_SAMPLES" ]; do
  connected=$(cat /sys/kernel/debug/ieee80211/phy0/wil6210/stations 2>/dev/null | grep -c connected)
  if [ "$connected" -eq 0 ]; then
    echo "Lost connection, stopping."
    break
  fi

  # Trigger one CSI/AoA measurement on the MikroTik/OpenWrt radio.
  echo -n -e '\x48\x8f\x5a\xdf\x02\x3b' | iw dev wlan0 vendor recv 0x001374 0x93 - >/dev/null 2>&1

  LAST_LINE=$(dmesg | tail -n 1)

  # Keep only measurement lines.
  echo "$LAST_LINE" | grep -q "Measurement:" || continue

  echo "$LAST_LINE" >> "$CSI_FILE"

  SAMPLES_DONE=$((SAMPLES_DONE+1))
  echo "Current CSI samples: $SAMPLES_DONE / $NUM_SAMPLES"
done

killall wpa_supplicant 2>/dev/null

echo "We got $SAMPLES_DONE CSI measurements."
echo "Saved to $CSI_FILE"
