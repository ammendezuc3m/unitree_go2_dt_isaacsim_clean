#!/bin/sh

PC_IP="$1"
PC_USER="$2"

PC_FILE="/home/nextnet/AlbertoDir/go2_dt/csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt"
KEY="/root/.ssh/id_rsa_dropbear"

MAC_BIN="/tmp/ap_mac.bin"
AP_IP="10.10.10.1"
AP_MAC_TEXT="b8:69:f4:d5:49:a9"

LOCAL_LOG="/tmp/csi_05s_single_local.txt"
LOCKDIR="/tmp/csi_05s_single.lock"

PERIOD_S="0.5"
AFTER_TRIGGER_SLEEP_S="0.2"

if [ -z "$PC_IP" ] || [ -z "$PC_USER" ]; then
  echo "[CSI][ERROR] Uso: $0 <PC_IP> <PC_USER>" >&2
  exit 1
fi

if ! mkdir "$LOCKDIR" 2>/dev/null; then
  echo "[CSI][ERROR] Ya hay otro streamer CSI ejecutándose: $LOCKDIR" >&2
  ps w | grep -E "stream_csi|vendor recv|iw dev wlan0 vendor|ssh|dbclient" | grep -v grep >&2 || true
  exit 1
fi

cleanup() {
  rm -rf "$LOCKDIR" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

check_mac_bin() {
  [ -f "$MAC_BIN" ] || return 1
  [ "$(wc -c < "$MAC_BIN")" = "6" ] || return 1
  return 0
}

is_connected() {
  iw dev wlan0 link 2>/dev/null | grep -q "Connected to"
}

ping_ok() {
  ping -I wlan0 -c 1 -W 1 "$AP_IP" >/dev/null 2>&1
}

valid_line() {
  line="$1"
  echo "$line" | grep -q "\[AOA\] Measurement:" || return 1
  echo "$line" | grep -q "Measurement: 0," || return 1
  echo "$line" | grep -q "$AP_MAC_TEXT" || return 1

  payload="$(echo "$line" | sed 's/^.*Measurement: //')"
  tokens="$(echo "$payload" | awk -F',' '{print NF}')"

  [ "$tokens" = "72" ] || return 1
  return 0
}

echo "[CSI] Target: ${PC_USER}@${PC_IP}:${PC_FILE}" >&2
echo "[CSI] Mode: SINGLE WRITER PIPE 0.5s BIN-MAC SAFE" >&2
echo "[CSI] Period: ${PERIOD_S}s" >&2
echo "[CSI] After trigger sleep: ${AFTER_TRIGGER_SLEEP_S}s" >&2
echo "[CSI] MAC_BIN=${MAC_BIN}" >&2

if ! check_mac_bin; then
  echo "[CSI][ERROR] MAC_BIN inválido: $MAC_BIN" >&2
  [ -f "$MAC_BIN" ] && hexdump -C "$MAC_BIN" >&2
  exit 1
fi

: > "$LOCAL_LOG"

dmesg -c >/tmp/dmesg_before_csi_stream.txt 2>/dev/null || true

while true; do
  echo "[CSI] Opening SSH append pipe." >&2

  (
    last_sent=""
    invalid_streak=0

    while true; do
      if ! is_connected; then
        echo "[CSI][WAIT] wlan0 no conectado" >&2
        sleep 2
        continue
      fi

      if ! ping_ok; then
        echo "[CSI][WAIT] ping STA->AP falla; no disparo vendor recv" >&2
        sleep 2
        continue
      fi

      dmesg -c >/tmp/dmesg_before_one_csi.txt 2>/dev/null || true

      cat "$MAC_BIN" | iw dev wlan0 vendor recv 0x001374 0x93 - >/dev/null 2>&1

      sleep "$AFTER_TRIGGER_SLEEP_S"

      LAST_LINE="$(dmesg | grep "\[AOA\] Measurement:" | tail -n 1)"

      if valid_line "$LAST_LINE"; then
        invalid_streak=0

        if [ "$LAST_LINE" != "$last_sent" ]; then
          last_sent="$LAST_LINE"
          echo "$LAST_LINE" >> "$LOCAL_LOG"
          printf "%s\n" "$LAST_LINE"
        fi
      else
        payload="$(echo "$LAST_LINE" | sed 's/^.*Measurement: //')"
        mac="$(echo "$payload" | awk -F',' '{print $3}')"
        tokens="$(echo "$payload" | awk -F',' '{print NF}')"
        echo "[CSI][DROP] tokens=${tokens:-0} mac=${mac:-none}" >&2

        invalid_streak=$((invalid_streak + 1))

        if [ "$invalid_streak" -ge 1 ]; then
          echo "[CSI][ERROR] primer CSI inválido; salgo para no romper WMI" >&2
          exit 2
        fi
      fi

      sleep "$PERIOD_S"
    done
  ) | ssh -i "$KEY" "$PC_USER@$PC_IP" "cat >> '$PC_FILE'"

  RC=$?
  echo "[CSI][WARN] pipe cerrado con rc=$RC" >&2

  if [ "$RC" = "2" ]; then
    echo "[CSI][ERROR] CSI inválido; no reintento para no ensuciar WMI" >&2
    exit 2
  fi

  sleep 1
done
