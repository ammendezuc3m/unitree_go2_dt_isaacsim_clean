#!/usr/bin/env bash
set -Eeuo pipefail

# ============================================================
# SETUP AP13 / STA12 - NUEVA TOPOLOGÍA
#
# Spark --eth--> AP .13 --60GHz--> STA .12 --eth--> Raspi
#
# Acceso de gestión:
#   Spark -> AP .13 por eth/switch:       192.168.1.13
#   Spark -> Raspi por WiFi UC3M:         163.117.140.252
#   Spark -> STA .12:                     vía Raspi eth, usando ssh jump manual
#
# Plano demo:
#   Spark enP7s7:      172.16.12.170/24
#   AP .13 br-lan:     172.16.12.1/24
#   AP .13 wlan0:      10.10.10.1/24
#   STA .12 wlan0:     10.10.10.2/24
#   STA .12 br-lan:    172.16.13.1/24
#   Raspi eth0:        172.16.13.100/24
# ============================================================

SPARK_IF="${SPARK_IF:-enP7s7}"

AP_HOST="${AP_HOST:-192.168.1.13}"
STA_HOST_FROM_RASPI="${STA_HOST_FROM_RASPI:-192.168.1.12}"

RASPI_MGMT_HOST="${RASPI_MGMT_HOST:-163.117.140.252}"
RASPI_USER="${RASPI_USER:-nextnet}"

SSID_60G="${SSID_60G:-TEST-LINK}"
CHANNEL_60G="${CHANNEL_60G:-2}"

SPARK_DEMO_IP="${SPARK_DEMO_IP:-172.16.12.170/24}"
SPARK_DEMO_IP_PLAIN="${SPARK_DEMO_IP_PLAIN:-172.16.12.170}"

SPARK_MGMT_IP="${SPARK_MGMT_IP:-192.168.1.170/24}"
SPARK_MGMT_IP_PLAIN="${SPARK_MGMT_IP_PLAIN:-192.168.1.170}"

RASPI_DEMO_IP="${RASPI_DEMO_IP:-172.16.13.100/24}"
RASPI_DEMO_IP_PLAIN="${RASPI_DEMO_IP_PLAIN:-172.16.13.100}"

AP_LAN_IP="${AP_LAN_IP:-172.16.12.1/24}"
AP_LAN_IP_PLAIN="${AP_LAN_IP_PLAIN:-172.16.12.1}"

STA_LAN_IP="${STA_LAN_IP:-172.16.13.1/24}"
STA_LAN_IP_PLAIN="${STA_LAN_IP_PLAIN:-172.16.13.1}"

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

log() { echo -e "\033[1;32m[setup-ap13-sta12-new]\033[0m $*"; }
warn() { echo -e "\033[1;33m[setup-ap13-sta12-new][WARN]\033[0m $*" >&2; }

ssh_ap() {
  ssh "${SSH_RADIO_OPTS[@]}" root@"${AP_HOST}" "$@"
}

ssh_raspi() {
  ssh -tt \
    -o ConnectTimeout=8 \
    -o StrictHostKeyChecking=accept-new \
    "${RASPI_USER}@${RASPI_MGMT_HOST}" "$@"
}

ssh_sta_via_raspi() {
  ssh -o ConnectTimeout=8 "${RASPI_USER}@${RASPI_MGMT_HOST}" \
    "ssh -o ConnectTimeout=5 -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa -o StrictHostKeyChecking=accept-new root@${STA_HOST_FROM_RASPI} '$*'"
}

configure_spark() {
  log "Configurando Spark ${SPARK_IF}: demo=${SPARK_DEMO_IP} mgmt=${SPARK_MGMT_IP}"

  # IP de gestión para hablar con AP .13 en 192.168.1.13.
  sudo ip addr add "${SPARK_MGMT_IP}" dev "${SPARK_IF}" 2>/dev/null || true

  # IP demo lado Spark/AP.
  sudo ip addr add "${SPARK_DEMO_IP}" dev "${SPARK_IF}" 2>/dev/null || true

  # Ahora Spark está físicamente del lado AP .13.
  # Para llegar a Raspi 172.16.13.0/24 debe ir vía AP LAN 172.16.12.1.
  sudo ip route replace 172.16.13.0/24 via "${AP_LAN_IP_PLAIN}" dev "${SPARK_IF}" src "${SPARK_DEMO_IP_PLAIN}"
  sudo ip route flush cache 2>/dev/null || true

  echo "=== Spark IPs en ${SPARK_IF} ==="
  ip -br addr show dev "${SPARK_IF}" || true

  echo
  echo "=== Ruta hacia AP gestión ==="
  ip route get "${AP_HOST}" || true

  echo
  echo "=== Ruta hacia Raspi demo ==="
  ip route get "${RASPI_DEMO_IP_PLAIN}" || true
}

configure_raspi_eth_for_sta_side() {
  log "Configurando Raspi por WiFi ${RASPI_MGMT_HOST}: eth0 demo ${RASPI_DEMO_IP}"

  ssh_raspi "
set -e

echo '=== Raspi interfaces antes ==='
ip -br a || true

# eth0 está conectada a la STA .12.
# Además de IPv6/lo que tenga, añadimos:
#   - IP temporal de gestión hacia STA .12: 192.168.1.100/24
#   - IP demo: 172.16.13.100/24
sudo ip link set eth0 up 2>/dev/null || true
sudo ip addr add 192.168.1.100/24 dev eth0 2>/dev/null || true
sudo ip addr add '${RASPI_DEMO_IP}' dev eth0 2>/dev/null || true

# Para volver hacia Spark 172.16.12.170, la puerta es STA .12 lado Raspi: 172.16.13.1
sudo ip route replace 172.16.12.0/24 via '${STA_LAN_IP_PLAIN}' dev eth0 src '${RASPI_DEMO_IP_PLAIN}'

echo
echo '=== Raspi interfaces después ==='
ip -br a || true

echo
echo '=== Raspi ruta a Spark demo ==='
ip route get '${SPARK_DEMO_IP_PLAIN}' || true

echo
echo '=== Ping Raspi -> STA gestión 192.168.1.12 ==='
ping -c 3 -W 2 '${STA_HOST_FROM_RASPI}' || true
"
}

reset_ap13() {
  log "Reset limpio AP .13 (${AP_HOST})"

  ssh_ap '
set -u

echo "=== AP before ==="
ps w | grep -E "hostapd|wpa_supplicant|iw dev wlan0 vendor|vendor recv|stream_csi|iperf3|tcpdump" | grep -v grep || true

killall -9 hostapd 2>/dev/null || true
killall -9 wpa_supplicant 2>/dev/null || true
killall -9 iw 2>/dev/null || true
killall -9 iperf3 2>/dev/null || true
killall -9 tcpdump 2>/dev/null || true
pkill -9 -f "stream_csi" 2>/dev/null || true
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
  [ -w "$WILDIR/reset" ] && echo 1 > "$WILDIR/reset" 2>/dev/null || true
fi

sleep 6
dmesg -c >/tmp/dmesg_after_setup_ap13_new_topology.txt 2>/dev/null || true
'
}

reset_sta12_via_raspi() {
  log "Reset limpio STA .12 vía Raspi WiFi -> eth"

  ssh_sta_via_raspi '
set -u

echo "=== STA before ==="
ps w | grep -E "hostapd|wpa_supplicant|iw dev wlan0 vendor|vendor recv|stream_csi|iperf3|tcpdump" | grep -v grep || true

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
  [ -w "$WILDIR/reset" ] && echo 1 > "$WILDIR/reset" 2>/dev/null || true
fi

sleep 6
dmesg -c >/tmp/dmesg_after_setup_sta12_new_topology.txt 2>/dev/null || true
'
}

write_radio_configs() {
  log "Escribiendo configs 60GHz AP13/STA12"

  ssh_ap "cat > /tmp/hostapd_ap13_60g.conf" <<EOF_AP
interface=wlan0
driver=nl80211
ssid=${SSID_60G}
hw_mode=ad
channel=${CHANNEL_60G}
auth_algs=1
ignore_broadcast_ssid=0
EOF_AP

  ssh_raspi "cat > /tmp/wpa_sta12_60g.conf.local <<'EOF_STA'
ctrl_interface=/var/run/wpa_supplicant
ap_scan=1

network={
    ssid=\"${SSID_60G}\"
    key_mgmt=NONE
    scan_ssid=1
}
EOF_STA

scp -O -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa -o StrictHostKeyChecking=accept-new /tmp/wpa_sta12_60g.conf.local root@${STA_HOST_FROM_RASPI}:/tmp/wpa_sta12_60g.conf
"
}

start_ap13() {
  log "Arrancando AP .13: radio 10.10.10.1 + LAN Spark 172.16.12.1"

  ssh_ap "
set -e

killall -9 hostapd 2>/dev/null || true

ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
ip neigh flush dev wlan0 2>/dev/null || true
sleep 2

ip link set wlan0 up
sleep 5
ip addr add '${AP_WLAN_IP}' dev wlan0 2>/dev/null || true

# AP .13 está conectado físicamente a Spark.
# Limpieza de IPs demo antiguas/nuevas para evitar que AP tenga ambos lados.
ip addr del 172.16.12.1/24 dev eth0 2>/dev/null || true
ip addr del 172.16.12.1/24 dev br-lan 2>/dev/null || true
ip addr del 172.16.13.1/24 dev eth0 2>/dev/null || true
ip addr del 172.16.13.1/24 dev br-lan 2>/dev/null || true

ip addr add '${AP_LAN_IP}' dev br-lan 2>/dev/null || true

hostapd -B /tmp/hostapd_ap13_60g.conf
sleep 6

echo 1 > /proc/sys/net/ipv4/ip_forward 2>/dev/null || true
iptables -P FORWARD ACCEPT 2>/dev/null || true
iptables -F FORWARD 2>/dev/null || true

# Para llegar a Raspi 172.16.13.0/24, cruzar por STA radio 10.10.10.2.
ip route replace 172.16.13.0/24 via '${STA_WLAN_IP_PLAIN}' dev wlan0

echo '=== AP .13 wlan0 ==='
ip addr show wlan0
echo
echo '=== AP .13 br-lan ==='
ip addr show br-lan || true
echo
echo '=== AP .13 routes ==='
ip route
echo
echo '=== AP .13 hostapd ==='
ps w | grep hostapd | grep -v grep || true
"
}

start_sta12_via_raspi() {
  log "Arrancando STA .12: radio 10.10.10.2 + LAN Raspi 172.16.13.1"

  ssh_sta_via_raspi "
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

# STA .12 está conectada físicamente a Raspi.
# Limpieza de IPs demo antiguas/nuevas para evitar que STA tenga ambos lados.
ip addr del 172.16.12.1/24 dev eth0 2>/dev/null || true
ip addr del 172.16.12.1/24 dev br-lan 2>/dev/null || true
ip addr del 172.16.13.1/24 dev eth0 2>/dev/null || true
ip addr del 172.16.13.1/24 dev br-lan 2>/dev/null || true

ip addr add '${STA_LAN_IP}' dev br-lan 2>/dev/null || true

wpa_supplicant -D nl80211 -i wlan0 -c /tmp/wpa_sta12_60g.conf -B
sleep 3

for i in \$(seq 1 45); do
  if iw dev wlan0 link 2>/dev/null | grep -q Connected; then
    echo '[STA .12] Conectada.'
    break
  fi
  echo '[STA .12] Esperando asociación' \$i/45
  sleep 1
done

echo 1 > /proc/sys/net/ipv4/ip_forward 2>/dev/null || true
iptables -P FORWARD ACCEPT 2>/dev/null || true
iptables -F FORWARD 2>/dev/null || true

# Para llegar a Spark 172.16.12.0/24, cruzar por AP radio 10.10.10.1.
ip route replace 172.16.12.0/24 via '${AP_WLAN_IP_PLAIN}' dev wlan0

echo '=== STA .12 link ==='
iw dev wlan0 link || true
echo
echo '=== STA .12 wlan0 ==='
ip addr show wlan0
echo
echo '=== STA .12 br-lan ==='
ip addr show br-lan || true
echo
echo '=== STA .12 routes ==='
ip route
"
}

verify() {
  log "Verificando topología nueva"

  echo
  echo "=== Spark -> AP .13 LAN ==="
  ping -c 5 -W 2 "${AP_LAN_IP_PLAIN}" || true

  echo
  echo "=== Spark -> Raspi demo ==="
  ip route get "${RASPI_DEMO_IP_PLAIN}" || true
  ping -c 5 -W 2 "${RASPI_DEMO_IP_PLAIN}" || true

  echo
  echo "=== Raspi -> Spark demo ==="
  ssh_raspi "
ping -c 5 -W 2 '${SPARK_DEMO_IP_PLAIN}' || true
ip route get '${SPARK_DEMO_IP_PLAIN}' || true
"

  echo
  echo "=== STA .12 -> AP .13 radio ==="
  ssh_sta_via_raspi "
iw dev wlan0 link || true
ping -I wlan0 -c 5 -W 2 '${AP_WLAN_IP_PLAIN}' || true
"

  echo
  echo "=== AP .13 -> STA .12 radio ==="
  ssh_ap "
ping -I wlan0 -c 5 -W 2 '${STA_WLAN_IP_PLAIN}' || true
"

  echo
  echo "=== Raspi sender check por WiFi ==="
  ssh_raspi "
test -x /home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh && echo sender_OK || echo sender_MISSING
test -e /dev/video0 && echo /dev/video0_OK || true
test -e /dev/video4 && echo /dev/video4_OK || true
"
}

main() {
  log "SETUP NUEVO: Raspi -> STA .12 -> 60GHz -> AP .13 -> Spark"

  configure_spark
  configure_raspi_eth_for_sta_side

  reset_ap13
  reset_sta12_via_raspi

  write_radio_configs
  start_ap13
  start_sta12_via_raspi

  configure_raspi_eth_for_sta_side

  verify

  log "Setup terminado."
}

main "$@"
