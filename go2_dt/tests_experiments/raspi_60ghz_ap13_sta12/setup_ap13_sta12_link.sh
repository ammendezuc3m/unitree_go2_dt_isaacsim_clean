#!/usr/bin/env bash
set -Eeuo pipefail

SPARK_IF="${SPARK_IF:-enP7s7}"

RASPI_MGMT_HOST="${RASPI_MGMT_HOST:-192.168.1.100}"
AP_HOST="${AP_HOST:-192.168.1.13}"
STA_HOST="${STA_HOST:-192.168.1.12}"

SSID_60G="${SSID_60G:-TEST-LINK}"
CHANNEL_60G="${CHANNEL_60G:-2}"
FREQ_60G="${FREQ_60G:-60480}"

RASPI_DEMO_IP="${RASPI_DEMO_IP:-172.16.13.100/24}"
RASPI_DEMO_IP_PLAIN="${RASPI_DEMO_IP_PLAIN:-172.16.13.100}"

SPARK_DEMO_IP="${SPARK_DEMO_IP:-172.16.12.170/24}"
SPARK_DEMO_IP_PLAIN="${SPARK_DEMO_IP_PLAIN:-172.16.12.170}"
SPARK_MGMT_IP="${SPARK_MGMT_IP:-192.168.1.170/24}"
SPARK_MGMT_IP_PLAIN="${SPARK_MGMT_IP_PLAIN:-192.168.1.170}"

AP_LAN_IP="${AP_LAN_IP:-172.16.13.1/24}"
AP_LAN_IP_PLAIN="${AP_LAN_IP_PLAIN:-172.16.13.1}"

STA_LAN_IP="${STA_LAN_IP:-172.16.12.1/24}"
STA_LAN_IP_PLAIN="${STA_LAN_IP_PLAIN:-172.16.12.1}"

AP_WLAN_IP="${AP_WLAN_IP:-10.10.10.1/24}"
AP_WLAN_IP_PLAIN="${AP_WLAN_IP_PLAIN:-10.10.10.1}"

STA_WLAN_IP="${STA_WLAN_IP:-10.10.10.2/24}"
STA_WLAN_IP_PLAIN="${STA_WLAN_IP_PLAIN:-10.10.10.2}"

SSH_RADIO_OPTS=(
  -o ConnectTimeout=5
  -o HostKeyAlgorithms=+ssh-rsa
  -o PubkeyAcceptedAlgorithms=+ssh-rsa
  -o StrictHostKeyChecking=accept-new
)

log() { echo -e "\033[1;32m[setup-ap13-sta12]\033[0m $*"; }

reset_radio() {
  local host="$1"
  local label="$2"

  log "Reset limpio radio ${label} (${host})"

  ssh "${SSH_RADIO_OPTS[@]}" root@"${host}" '
echo "=== before ==="
ps w | grep -E "hostapd|wpa_supplicant|iw dev wlan0 vendor|vendor recv|stream_csi|iperf3|tcpdump" | grep -v grep || true

killall -9 hostapd 2>/dev/null || true
killall -9 wpa_supplicant 2>/dev/null || true
killall -9 iw 2>/dev/null || true
killall -9 iperf3 2>/dev/null || true
killall -9 tcpdump 2>/dev/null || true

pkill -9 -f "stream_csi" 2>/dev/null || true
pkill -9 -f "live_csi_stream" 2>/dev/null || true
pkill -9 -f "vendor recv" 2>/dev/null || true
pkill -9 -f "iw dev wlan0 vendor" 2>/dev/null || true

rm -rf /var/run/wpa_supplicant
mkdir -p /var/run/wpa_supplicant

ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
ip neigh flush dev wlan0 2>/dev/null || true

sleep 2

WILDIR=$(ls -d /sys/kernel/debug/ieee80211/phy*/wil6210 2>/dev/null | tail -n 1 || true)
echo "WILDIR=$WILDIR"
if [ -n "$WILDIR" ]; then
  echo "FW=$(cat "$WILDIR/fw_version" 2>/dev/null || true)"
  echo "recovery_count=$(cat "$WILDIR/recovery_count" 2>/dev/null || true)"
  echo "status_msg=$(cat "$WILDIR/status_msg" 2>/dev/null || true)"
  if [ -w "$WILDIR/reset" ]; then
    echo 1 > "$WILDIR/reset" 2>/dev/null || true
    echo "debugfs reset sent"
  fi
fi

sleep 6

dmesg -c >/tmp/dmesg_after_setup_ap13_sta12.txt 2>/dev/null || true

echo "=== after ==="
ps w | grep -E "hostapd|wpa_supplicant|iw dev wlan0 vendor|vendor recv|stream_csi|iperf3|tcpdump" | grep -v grep || echo "radio limpia"
'
}

configure_spark() {
  log "Configurando Spark ${SPARK_IF}: ${SPARK_DEMO_IP}"

  # Mantener simultáneamente:
  # - IP de gestión por switch: 192.168.1.170/24
  # - IP demo lado STA .12:     172.16.12.170/24
  sudo ip addr add "${SPARK_MGMT_IP}" dev "${SPARK_IF}" 2>/dev/null || true
  sudo ip addr add "${SPARK_DEMO_IP}" dev "${SPARK_IF}" 2>/dev/null || true

  sudo ip route replace 172.16.13.0/24 via "${STA_LAN_IP_PLAIN}" dev "${SPARK_IF}" src "${SPARK_DEMO_IP_PLAIN}"

  ip route get "${RASPI_DEMO_IP_PLAIN}" || true
}

configure_raspi() {
  log "Configurando Raspi: ${RASPI_DEMO_IP}, ruta hacia Spark por ${AP_LAN_IP_PLAIN}"

  ssh system@"${RASPI_MGMT_HOST}" "
set -e
DEV=\$(ip route get 192.168.1.170 2>/dev/null | awk '{for(i=1;i<=NF;i++) if(\$i==\"dev\") print \$(i+1)}' | head -n1)
if [ -z \"\$DEV\" ]; then
  DEV=\$(ip route | awk '/192.168.1.0\\/24/ {print \$3; exit}')
fi
if [ -z \"\$DEV\" ]; then
  DEV=\$(ip -o link show | awk -F': ' '\$2 != \"lo\" {print \$2; exit}')
fi

echo \"RASPI_DEV=\$DEV\"

sudo ip addr add '${RASPI_DEMO_IP}' dev \"\$DEV\" 2>/dev/null || true
sudo ip route replace 172.16.12.0/24 via '${AP_LAN_IP_PLAIN}' dev \"\$DEV\" src '${RASPI_DEMO_IP_PLAIN}'

ip addr show \"\$DEV\"
ip route get '${SPARK_DEMO_IP_PLAIN}' || true
"
}

write_radio_configs() {
  log "Escribiendo configs 60GHz: AP .13 / STA .12"

  ssh "${SSH_RADIO_OPTS[@]}" root@"${AP_HOST}" "cat > /tmp/hostapd_ap13_60g.conf" <<EOF_AP
interface=wlan0
driver=nl80211
ssid=${SSID_60G}
hw_mode=ad
channel=${CHANNEL_60G}
auth_algs=1
ignore_broadcast_ssid=0
EOF_AP

  ssh "${SSH_RADIO_OPTS[@]}" root@"${STA_HOST}" "cat > /tmp/wpa_sta12_60g.conf" <<EOF_STA
ctrl_interface=/var/run/wpa_supplicant
ap_scan=1

network={
    ssid="${SSID_60G}"
    key_mgmt=NONE
    scan_ssid=1
}
EOF_STA
}

start_ap13() {
  log "Arrancando AP .13"

  ssh "${SSH_RADIO_OPTS[@]}" root@"${AP_HOST}" "
set -e

killall -9 hostapd 2>/dev/null || true
ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
ip neigh flush dev wlan0 2>/dev/null || true
sleep 2

ip link set wlan0 up
sleep 5

ip addr add '${AP_WLAN_IP}' dev wlan0 2>/dev/null || true

ip addr del '${AP_LAN_IP}' dev eth0 2>/dev/null || true
ip addr add '${AP_LAN_IP}' dev br-lan 2>/dev/null || true

hostapd -B /tmp/hostapd_ap13_60g.conf
sleep 6

echo 1 > /proc/sys/net/ipv4/ip_forward 2>/dev/null || true
iptables -P FORWARD ACCEPT 2>/dev/null || true
iptables -F FORWARD 2>/dev/null || true

ip route replace 172.16.12.0/24 via '${STA_WLAN_IP_PLAIN}' dev wlan0

echo '=== AP .13 wlan0 ==='
ip addr show wlan0
echo
echo '=== AP .13 routes ==='
ip route
echo
echo '=== AP .13 hostapd ==='
ps w | grep hostapd | grep -v grep || true
"
}

start_sta12() {
  log "Arrancando STA .12"

  ssh "${SSH_RADIO_OPTS[@]}" root@"${STA_HOST}" "
set -e

killall -9 wpa_supplicant 2>/dev/null || true
rm -rf /var/run/wpa_supplicant
mkdir -p /var/run/wpa_supplicant

ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
ip neigh flush dev wlan0 2>/dev/null || true
sleep 2

ip link set wlan0 up
sleep 5

ip addr add '${STA_WLAN_IP}' dev wlan0 2>/dev/null || true

ip addr del '${STA_LAN_IP}' dev eth0 2>/dev/null || true
ip addr add '${STA_LAN_IP}' dev br-lan 2>/dev/null || true

wpa_supplicant -D nl80211 -i wlan0 -c /tmp/wpa_sta12_60g.conf -B
sleep 3

for i in \$(seq 1 40); do
  if iw dev wlan0 link 2>/dev/null | grep -q 'Connected to'; then
    echo '[STA .12] Conectada.'
    break
  fi
  echo '[STA .12] Esperando asociación' \$i/40
  sleep 1
done

echo 1 > /proc/sys/net/ipv4/ip_forward 2>/dev/null || true
iptables -P FORWARD ACCEPT 2>/dev/null || true
iptables -F FORWARD 2>/dev/null || true

ip route replace 172.16.13.0/24 via '${AP_WLAN_IP_PLAIN}' dev wlan0

echo '=== STA .12 link ==='
iw dev wlan0 link || true
echo
echo '=== STA .12 wlan0 ==='
ip addr show wlan0
echo
echo '=== STA .12 routes ==='
ip route
"
}

verify() {
  log "Verificando enlace AP .13 / STA .12"

  echo
  echo "=== STA .12 -> AP .13 radio ==="
  ssh "${SSH_RADIO_OPTS[@]}" root@"${STA_HOST}" "
iw dev wlan0 link || true
ping -I wlan0 -c 5 -W 2 '${AP_WLAN_IP_PLAIN}' || true
"

  echo
  echo "=== AP .13 -> STA .12 radio ==="
  ssh "${SSH_RADIO_OPTS[@]}" root@"${AP_HOST}" "
ping -I wlan0 -c 5 -W 2 '${STA_WLAN_IP_PLAIN}' || true
"

  echo
  echo "=== Spark -> Raspi demo ==="
  ping -c 5 -W 2 "${RASPI_DEMO_IP_PLAIN}" || true

  echo
  echo "=== Raspi -> Spark demo ==="
  ssh system@"${RASPI_MGMT_HOST}" "
ping -c 5 -W 2 '${SPARK_DEMO_IP_PLAIN}' || true
ip route get '${SPARK_DEMO_IP_PLAIN}' || true
"

  echo
  echo "=== Sender Raspi check ==="
  ssh system@"${RASPI_MGMT_HOST}" "
test -x /home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh && echo sender_OK || echo sender_MISSING
grep -nE 'ENABLE_CAMERA|ENABLE_MP4|ENABLE_IPERF|IPERF_MODE|format=I420|mp4-loop|iperf-loop' /home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh 2>/dev/null || true
"
}

main() {
  log "SETUP AP .13 / STA .12 para pipeline Raspi -> Spark"

  configure_spark
  configure_raspi

  reset_radio "${AP_HOST}" "AP13"
  reset_radio "${STA_HOST}" "STA12"

  write_radio_configs
  start_ap13
  start_sta12
  verify

  log "Setup terminado."
}

main "$@"
