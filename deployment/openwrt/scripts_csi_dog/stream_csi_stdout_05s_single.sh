#!/bin/sh
set -u

# Live CSI producer for the full demo.
# Runs ON the STA MikroTik/OpenWrt device and writes valid CSI measurements
# to stdout. The Spark-side launcher reaches this script over SSH and pipes
# stdout into realtime_inputs/live_csi_stream.txt.

MAC_BIN="${CSI_MAC_BIN:-/tmp/csi_peer_mac.bin}"
PERIOD_S="${CSI_PERIOD_S:-0.5}"
AFTER_TRIGGER_SLEEP_S="${CSI_TRIGGER_DELAY_S:-0.20}"
LOCKDIR="${CSI_LOCKDIR:-/tmp/csi_stdout_05s.lock}"

if ! mkdir "$LOCKDIR" 2>/dev/null; then
  echo "[CSI-STDOUT][ERROR] Another CSI streamer is already running: $LOCKDIR" >&2
  exit 1
fi

cleanup() {
  rm -rf "$LOCKDIR" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

is_connected() {
  iw dev wlan0 link 2>/dev/null | grep -q "Connected to"
}

prepare_peer_mac() {
  BSSID="$(iw dev wlan0 link 2>/dev/null | awk '/Connected to/ {print $3; exit}')"
  [ -n "$BSSID" ] || return 1
  ESCAPED="$(echo "$BSSID" | awk -F: '{printf "\\x%s\\x%s\\x%s\\x%s\\x%s\\x%s", $1,$2,$3,$4,$5,$6}')"
  printf '%b' "$ESCAPED" > "$MAC_BIN"
  [ "$(wc -c < "$MAC_BIN")" -eq 6 ] || return 1
  echo "[CSI-STDOUT] peer BSSID=$BSSID" >&2
}

echo "[CSI-STDOUT] Runs on MikroTik/OpenWrt STA; period=${PERIOD_S}s" >&2

last_sent=""
while true; do
  if ! is_connected; then
    echo "[CSI-STDOUT][WAIT] wlan0 not associated" >&2
    sleep 2
    continue
  fi

  if [ ! -f "$MAC_BIN" ] || [ "$(wc -c < "$MAC_BIN" 2>/dev/null || echo 0)" -ne 6 ]; then
    prepare_peer_mac || {
      echo "[CSI-STDOUT][WARN] could not derive peer BSSID" >&2
      sleep 2
      continue
    }
  fi

  cat "$MAC_BIN" | iw dev wlan0 vendor recv 0x001374 0x93 - >/dev/null 2>&1
  sleep "$AFTER_TRIGGER_SLEEP_S"

  LAST_LINE="$(dmesg | grep "\[AOA\] Measurement:" | tail -n 1)"
  echo "$LAST_LINE" | grep -q "Measurement:" || {
    sleep "$PERIOD_S"
    continue
  }

  [ "$LAST_LINE" = "$last_sent" ] && {
    sleep "$PERIOD_S"
    continue
  }

  payload="$(echo "$LAST_LINE" | sed 's/^.*Measurement: //')"
  tokens="$(echo "$payload" | awk -F',' '{print NF}')"
  [ "$tokens" = "72" ] || {
    echo "[CSI-STDOUT][DROP] tokens=$tokens" >&2
    sleep "$PERIOD_S"
    continue
  }

  last_sent="$LAST_LINE"
  printf "%s\n" "$LAST_LINE"
  sleep "$PERIOD_S"
done
