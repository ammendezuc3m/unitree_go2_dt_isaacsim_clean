#!/usr/bin/env bash
set -Eeuo pipefail

AP_HOST="${AP_HOST:-192.168.1.12}"
STA_HOST="${STA_HOST:-192.168.1.13}"

AP_USER="${AP_USER:-root}"
STA_USER="${STA_USER:-root}"

SSID_60G="${SSID_60G:-TEST-LINK}"
CHANNEL_60G="${CHANNEL_60G:-2}"
FREQ_60G="${FREQ_60G:-60480}"

AP_WLAN_IP="${AP_WLAN_IP:-10.10.10.1/24}"
AP_WLAN_IP_PLAIN="${AP_WLAN_IP_PLAIN:-10.10.10.1}"

STA_WLAN_IP="${STA_WLAN_IP:-10.10.10.2/24}"
STA_WLAN_IP_PLAIN="${STA_WLAN_IP_PLAIN:-10.10.10.2}"

SSH_OPTS=(
  -o BatchMode=yes
  -o ConnectTimeout=5
  -o HostKeyAlgorithms=+ssh-rsa
  -o PubkeyAcceptedAlgorithms=+ssh-rsa
  -o StrictHostKeyChecking=accept-new
)

log() { echo -e "\033[1;32m[wmi-recover]\033[0m $*"; }
warn() { echo -e "\033[1;33m[wmi-recover][WARN]\033[0m $*" >&2; }

radio_reset_side() {
  local label="$1"
  local host="$2"
  local user="$3"

  log "Reset radio ${label} ${host}"

  ssh "${SSH_OPTS[@]}" "${user}@${host}" '
echo "=== before processes ==="
ps w | grep -E "hostapd|wpa_supplicant|iw dev wlan0 vendor|vendor recv|stream_csi|iperf3|tcpdump" | grep -v grep || true

echo
echo "=== kill radio/csi/users ==="
killall -9 hostapd 2>/dev/null || true
killall -9 wpa_supplicant 2>/dev/null || true
killall -9 iw 2>/dev/null || true
killall -9 iperf3 2>/dev/null || true
killall -9 tcpdump 2>/dev/null || true
killall -9 ssh 2>/dev/null || true
killall -9 dbclient 2>/dev/null || true

pkill -9 -f "stream_csi" 2>/dev/null || true
pkill -9 -f "live_csi_stream" 2>/dev/null || true
pkill -9 -f "vendor recv" 2>/dev/null || true
pkill -9 -f "iw dev wlan0 vendor" 2>/dev/null || true
pkill -9 -f "id_rsa_dropbear" 2>/dev/null || true

rm -rf /var/run/wpa_supplicant
mkdir -p /var/run/wpa_supplicant

rm -rf /tmp/csi_*.lock 2>/dev/null || true
rm -f /tmp/csi_stream_*.pid /tmp/csi_stream_*.out /tmp/csi_*local*.txt 2>/dev/null || true

echo
echo "=== wlan0 down/flush ==="
ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
ip neigh flush dev wlan0 2>/dev/null || true

sleep 2

echo
echo "=== wil6210 reset ==="
WILDIR=$(ls -d /sys/kernel/debug/ieee80211/phy*/wil6210 2>/dev/null | tail -n 1 || true)
echo "WILDIR=$WILDIR"

if [ -n "$WILDIR" ]; then
  echo "FW=$(cat "$WILDIR/fw_version" 2>/dev/null || true)"
  echo "recovery_count=$(cat "$WILDIR/recovery_count" 2>/dev/null || true)"
  echo "status_msg=$(cat "$WILDIR/status_msg" 2>/dev/null || true)"

  if [ -w "$WILDIR/reset" ]; then
    echo 1 > "$WILDIR/reset" 2>/dev/null || true
    echo "debugfs reset sent"
  else
    echo "debugfs reset not writable"
  fi
fi

sleep 6

dmesg -c >/tmp/dmesg_after_wmi_recover.txt 2>/dev/null || true

echo
echo "=== after reset ==="
ip addr show wlan0 || true
ps w | grep -E "hostapd|wpa_supplicant|iw dev wlan0 vendor|vendor recv|stream_csi" | grep -v grep || true
'
}

write_configs() {
  log "Escribiendo config AP .12 y STA .13"

  ssh "${SSH_OPTS[@]}" "${AP_USER}@${AP_HOST}" "cat > /tmp/hostapd_60g_ap12.conf" <<EOF_AP
interface=wlan0
driver=nl80211
ssid=${SSID_60G}
hw_mode=ad
channel=${CHANNEL_60G}
auth_algs=1
ignore_broadcast_ssid=0
EOF_AP

  ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" "cat > /tmp/wpa_60g_sta13.conf" <<EOF_STA
ctrl_interface=/var/run/wpa_supplicant
ap_scan=1

network={
    ssid="${SSID_60G}"
    key_mgmt=NONE
    scan_ssid=1
    scan_freq=${FREQ_60G}
    freq_list=${FREQ_60G}
}
EOF_STA
}

start_ap() {
  log "Arrancando AP .12"

  ssh "${SSH_OPTS[@]}" "${AP_USER}@${AP_HOST}" "
set -u
ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
sleep 2

ip link set wlan0 up
sleep 5

ip addr add '${AP_WLAN_IP}' dev wlan0 2>/dev/null || true
hostapd -B /tmp/hostapd_60g_ap12.conf
sleep 6

echo '=== AP iw info ==='
iw dev wlan0 info || true

echo
echo '=== AP wlan0 ==='
ip addr show wlan0 || true

echo
echo '=== AP hostapd ==='
ps w | grep hostapd | grep -v grep || true
"
}

start_sta() {
  log "Arrancando STA .13"

  ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" "
set -u
killall -9 wpa_supplicant 2>/dev/null || true
rm -rf /var/run/wpa_supplicant
mkdir -p /var/run/wpa_supplicant

ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
sleep 2

ip link set wlan0 up
sleep 5

ip addr add '${STA_WLAN_IP}' dev wlan0 2>/dev/null || true
wpa_supplicant -D nl80211 -i wlan0 -c /tmp/wpa_60g_sta13.conf -B
sleep 3

for i in \$(seq 1 40); do
  if iw dev wlan0 link 2>/dev/null | grep -q 'Connected to'; then
    echo '[STA] Conectada.'
    break
  fi
  echo '[STA] Esperando asociación' \$i/40
  sleep 1
done

echo '=== STA iw link ==='
iw dev wlan0 link || true

echo
echo '=== STA wlan0 ==='
ip addr show wlan0 || true
"
}

configure_routes() {
  log "Configurando rutas AP/STA"

  ssh "${SSH_OPTS[@]}" "${AP_USER}@${AP_HOST}" "
ip route replace 172.16.13.0/24 via ${STA_WLAN_IP_PLAIN} dev wlan0
ip route
"

  ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" "
ip route replace 172.16.12.0/24 via ${AP_WLAN_IP_PLAIN} dev wlan0
ip route
"
}

verify_link() {
  log "Verificando enlace"

  echo
  echo "=== STA -> AP radio ==="
  ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" "
iw dev wlan0 link || true
ping -I wlan0 -c 5 -W 2 ${AP_WLAN_IP_PLAIN}
"

  echo
  echo "=== AP -> STA radio ==="
  ssh "${SSH_OPTS[@]}" "${AP_USER}@${AP_HOST}" "
ping -I wlan0 -c 5 -W 2 ${STA_WLAN_IP_PLAIN}
"

  echo
  echo "=== dmesg STA clean check ==="
  ssh "${SSH_OPTS[@]}" "${STA_USER}@${STA_HOST}" "
dmesg | grep -Ei 'wmi ring full|halp vote timed out|disconnect|connect|error|fail' | tail -n 40 || true
"
}

main() {
  log "Recuperación WMI 60GHz: .12 AP, .13 STA"

  radio_reset_side "AP12" "${AP_HOST}" "${AP_USER}"
  radio_reset_side "STA13" "${STA_HOST}" "${STA_USER}"

  write_configs
  start_ap
  start_sta
  configure_routes
  verify_link

  log "Recuperación terminada."
}

main "$@"
