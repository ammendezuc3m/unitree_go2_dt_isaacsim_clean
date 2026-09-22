#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GO2_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SPARK_IFACE="${SPARK_IFACE:-enP7s7}"
SPARK_DEMO_IP="${SPARK_DEMO_IP:-172.16.13.170}"

RASPI_MGMT_IP="${RASPI_MGMT_IP:-192.168.1.100}"
RASPI_USER="${RASPI_USER:-system}"
RASPI_DEMO_IP="${RASPI_DEMO_IP:-172.16.12.100}"

AP_HOST="${AP_HOST:-192.168.1.12}"
STA_HOST="${STA_HOST:-192.168.1.13}"

AP_WLAN_IP="${AP_WLAN_IP:-10.10.10.1}"
STA_WLAN_IP="${STA_WLAN_IP:-10.10.10.2}"

AP_DEMO_ETH_IP="${AP_DEMO_ETH_IP:-172.16.12.1}"
STA_DEMO_ETH_IP="${STA_DEMO_ETH_IP:-172.16.13.1}"

SSID_60G="${SSID_60G:-TEST-LINK}"
CHANNEL_60G="${CHANNEL_60G:-2}"
FREQ_60G="${FREQ_60G:-60480}"

SSH_ANT_OPTS=(
  -o HostKeyAlgorithms=+ssh-rsa
  -o PubkeyAcceptedAlgorithms=+ssh-rsa
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=5
)

log() { echo -e "\033[1;32m[raspi-setup]\033[0m $*"; }
warn() { echo -e "\033[1;33m[raspi-setup][WARN]\033[0m $*" >&2; }
err() { echo -e "\033[1;31m[raspi-setup][ERROR]\033[0m $*" >&2; }

ssh_ant() {
  local host="$1"
  shift
  ssh "${SSH_ANT_OPTS[@]}" "root@${host}" "$@"
}

ssh_raspi() {
  ssh -o StrictHostKeyChecking=accept-new "${RASPI_USER}@${RASPI_MGMT_IP}" "$@"
}

wait_ssh_ant() {
  local host="$1"
  local name="$2"
  log "Comprobando SSH ${name} ${host}..."
  for i in $(seq 1 20); do
    if ssh_ant "$host" "echo ok" >/dev/null 2>&1; then
      log "SSH OK ${name}"
      return 0
    fi
    sleep 1
  done
  err "No hay SSH con ${name} ${host}"
  exit 1
}

wait_ssh_raspi() {
  log "Comprobando SSH Raspi ${RASPI_USER}@${RASPI_MGMT_IP}..."
  for i in $(seq 1 20); do
    if ssh_raspi "echo ok" >/dev/null 2>&1; then
      log "SSH OK Raspi"
      return 0
    fi
    sleep 1
  done
  err "No hay SSH con Raspi"
  exit 1
}

configure_spark_ip_routes() {
  log "Configurando IP demo en Spark ${SPARK_IFACE}: ${SPARK_DEMO_IP}/24"

  sudo ip addr add "${SPARK_DEMO_IP}/24" dev "${SPARK_IFACE}" 2>/dev/null || true
  sudo ip route replace 172.16.12.0/24 via "${STA_DEMO_ETH_IP}" dev "${SPARK_IFACE}" src "${SPARK_DEMO_IP}"
  sudo ip route flush cache

  echo
  echo "=== Spark route to Raspi demo ==="
  ip route get "${RASPI_DEMO_IP}" || true
}

clean_antennas() {
  for H in "${AP_HOST}" "${STA_HOST}"; do
    log "Limpiando antena ${H}..."
    ssh_ant "$H" '
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
[ -n "$WILDIR" ] && echo "FW=$(cat "$WILDIR/fw_version" 2>/dev/null || true)"

if [ -n "$WILDIR" ] && [ -w "$WILDIR/reset" ]; then
  echo 1 > "$WILDIR/reset" 2>/dev/null || true
  sleep 5
fi
'
  done
}

configure_ap12() {
  log "Configurando .12 como AP 60 GHz..."

  ssh_ant "${AP_HOST}" "
cat >/tmp/hostapd_60g_ap12.conf <<EOF_AP
interface=wlan0
driver=nl80211
ssid=${SSID_60G}
hw_mode=ad
channel=${CHANNEL_60G}
auth_algs=1
ignore_broadcast_ssid=0
EOF_AP

ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
sleep 2

ip link set wlan0 up
sleep 5

ip addr add ${AP_WLAN_IP}/24 dev wlan0 2>/dev/null || true
ip addr add ${AP_DEMO_ETH_IP}/24 dev br-lan 2>/dev/null || ip addr add ${AP_DEMO_ETH_IP}/24 dev eth0 2>/dev/null || true

echo 1 > /proc/sys/net/ipv4/ip_forward
for f in /proc/sys/net/ipv4/conf/*/rp_filter; do echo 0 > \$f 2>/dev/null || true; done

ip route replace 172.16.13.0/24 via ${STA_WLAN_IP} dev wlan0

hostapd -B /tmp/hostapd_60g_ap12.conf
sleep 8

echo '=== AP .12 wlan0 ==='
ip addr show wlan0
echo
echo '=== AP .12 br-lan/eth0 ==='
ip addr show br-lan 2>/dev/null || ip addr show eth0
echo
iw dev wlan0 info || true
"
}

configure_sta13() {
  log "Configurando .13 como STA 60 GHz..."

  ssh_ant "${STA_HOST}" "
cat >/tmp/wpa_60g_sta13.conf <<EOF_STA
ctrl_interface=/var/run/wpa_supplicant
ap_scan=1

network={
    ssid=\"${SSID_60G}\"
    key_mgmt=NONE
    scan_ssid=1
    scan_freq=${FREQ_60G}
    freq_list=${FREQ_60G}
}
EOF_STA

killall -9 wpa_supplicant 2>/dev/null || true
rm -rf /var/run/wpa_supplicant
mkdir -p /var/run/wpa_supplicant

ip link set wlan0 down 2>/dev/null || true
ip addr flush dev wlan0 2>/dev/null || true
sleep 2

ip link set wlan0 up
sleep 5

ip addr add ${STA_WLAN_IP}/24 dev wlan0 2>/dev/null || true
ip addr add ${STA_DEMO_ETH_IP}/24 dev br-lan 2>/dev/null || ip addr add ${STA_DEMO_ETH_IP}/24 dev eth0 2>/dev/null || true

echo 1 > /proc/sys/net/ipv4/ip_forward
for f in /proc/sys/net/ipv4/conf/*/rp_filter; do echo 0 > \$f 2>/dev/null || true; done

ip route replace 172.16.12.0/24 via ${AP_WLAN_IP} dev wlan0

wpa_supplicant -D nl80211 -i wlan0 -c /tmp/wpa_60g_sta13.conf -B
sleep 3

for i in \$(seq 1 40); do
  if iw dev wlan0 link 2>/dev/null | grep -q 'Connected to'; then
    echo '[STA .13] Conectada.'
    break
  fi
  echo '[STA .13] Esperando asociación' \$i/40
  sleep 1
done

echo '=== STA .13 link ==='
iw dev wlan0 link || true
echo
echo '=== STA .13 wlan0 ==='
ip addr show wlan0
echo
echo '=== STA .13 br-lan/eth0 ==='
ip addr show br-lan 2>/dev/null || ip addr show eth0
"
}

configure_raspi_routes_and_sender() {
  log "Configurando IP/rutas demo en Raspi y creando script TX..."

  ssh_raspi "bash -s" <<EOF_RASPI
set -Eeuo pipefail

sudo ip addr add ${RASPI_DEMO_IP}/24 dev eth0 2>/dev/null || true
sudo ip route replace 172.16.13.0/24 via ${AP_DEMO_ETH_IP} dev eth0 src ${RASPI_DEMO_IP}

mkdir -p /home/${RASPI_USER}/raspi_60ghz_demo/logs

cat > /home/${RASPI_USER}/raspi_60ghz_demo/raspi_60ghz_sender.sh <<'EOF_TX'
#!/usr/bin/env bash
set -Eeuo pipefail

SPARK_IP="\${SPARK_IP:-172.16.13.170}"

CAMERA_DEVICE="\${CAMERA_DEVICE:-/dev/video4}"
CAMERA_WIDTH="\${CAMERA_WIDTH:-424}"
CAMERA_HEIGHT="\${CAMERA_HEIGHT:-240}"
CAMERA_FPS="\${CAMERA_FPS:-15}"
CAMERA_BITRATE_KBPS="\${CAMERA_BITRATE_KBPS:-2000}"
CAMERA_PORT="\${CAMERA_PORT:-6000}"

MP4_FILE="\${MP4_FILE:-/home/system/golden_test.mp4}"
MP4_WIDTH="\${MP4_WIDTH:-1280}"
MP4_HEIGHT="\${MP4_HEIGHT:-720}"
MP4_FPS="\${MP4_FPS:-15}"
MP4_BITRATE_KBPS="\${MP4_BITRATE_KBPS:-20000}"
MP4_PORT="\${MP4_PORT:-6002}"
ENABLE_MP4="\${ENABLE_MP4:-1}"

IPERF_PORT="\${IPERF_PORT:-5201}"
IPERF_BITRATE="\${IPERF_BITRATE:-100M}"
IPERF_MODE="\${IPERF_MODE:-udp}"
IPERF_DURATION="\${IPERF_DURATION:-3600}"
IPERF_UDP_LENGTH="\${IPERF_UDP_LENGTH:-1470}"

LOG_DIR="\${LOG_DIR:-/home/system/raspi_60ghz_demo/logs}"
mkdir -p "\${LOG_DIR}"

echo "[raspi-tx] Cleaning old processes..."
pkill -f "gst-launch-1.0.*udpsink.*\${CAMERA_PORT}" 2>/dev/null || true
pkill -f "gst-launch-1.0.*udpsink.*\${MP4_PORT}" 2>/dev/null || true
pkill -f "iperf3.*\${SPARK_IP}" 2>/dev/null || true
killall -9 iperf3 2>/dev/null || true

echo "[raspi-tx] Target Spark: \${SPARK_IP}"
echo "[raspi-tx] Camera: \${CAMERA_DEVICE} \${CAMERA_WIDTH}x\${CAMERA_HEIGHT}@\${CAMERA_FPS}, \${CAMERA_BITRATE_KBPS} kbps -> UDP/RTP \${CAMERA_PORT}"
echo "[raspi-tx] MP4: enable=\${ENABLE_MP4}, \${MP4_FILE}, \${MP4_BITRATE_KBPS} kbps -> UDP/RTP \${MP4_PORT}"
echo "[raspi-tx] iperf: mode=\${IPERF_MODE}, bitrate=\${IPERF_BITRATE}, port=\${IPERF_PORT}"

echo "[raspi-tx] Starting camera stream..."
gst-launch-1.0 -v \
  v4l2src device="\${CAMERA_DEVICE}" do-timestamp=true ! \
  video/x-raw,format=YUY2,width="\${CAMERA_WIDTH}",height="\${CAMERA_HEIGHT}",framerate="\${CAMERA_FPS}/1" ! \
  videoconvert ! videoscale ! videorate ! \
  video/x-raw,width="\${CAMERA_WIDTH}",height="\${CAMERA_HEIGHT}",framerate="\${CAMERA_FPS}/1" ! \
  queue max-size-buffers=4 leaky=downstream ! \
  x264enc tune=zerolatency speed-preset=ultrafast bitrate="\${CAMERA_BITRATE_KBPS}" key-int-max="\${CAMERA_FPS}" bframes=0 byte-stream=true ! \
  h264parse config-interval=1 ! \
  rtph264pay config-interval=1 pt=96 mtu=1200 ! \
  udpsink host="\${SPARK_IP}" port="\${CAMERA_PORT}" sync=false async=false \
  >"\${LOG_DIR}/camera_tx.log" 2>&1 &

echo \$! > "\${LOG_DIR}/camera_tx.pid"

if [[ "\${ENABLE_MP4}" == "1" && -f "\${MP4_FILE}" ]]; then
  echo "[raspi-tx] Starting MP4 stream..."
  gst-launch-1.0 -v \
    filesrc location="\${MP4_FILE}" ! \
    qtdemux name=demux demux.video_0 ! queue ! decodebin ! \
    videoconvert ! videoscale ! videorate ! \
    video/x-raw,width="\${MP4_WIDTH}",height="\${MP4_HEIGHT}",framerate="\${MP4_FPS}/1" ! \
    identity sync=true ! \
    queue max-size-buffers=8 leaky=downstream ! \
    x264enc tune=zerolatency pass=cbr bitrate="\${MP4_BITRATE_KBPS}" speed-preset=ultrafast key-int-max="\${MP4_FPS}" bframes=0 byte-stream=true sliced-threads=true vbv-buf-capacity=1000 option-string="nal-hrd=cbr:force-cfr=1" ! \
    h264parse config-interval=1 ! \
    rtph264pay config-interval=1 pt=96 mtu=1200 ! \
    udpsink host="\${SPARK_IP}" port="\${MP4_PORT}" sync=false async=false \
    >"\${LOG_DIR}/mp4_tx.log" 2>&1 &

  echo \$! > "\${LOG_DIR}/mp4_tx.pid"
else
  echo "[raspi-tx] MP4 disabled or file missing."
fi

sleep 2

echo "[raspi-tx] Starting iperf client..."
if [[ "\${IPERF_MODE}" == "tcp" ]]; then
  iperf3 -c "\${SPARK_IP}" -p "\${IPERF_PORT}" -b "\${IPERF_BITRATE}" -t "\${IPERF_DURATION}" -i 1 \
    >"\${LOG_DIR}/iperf_client.log" 2>&1 &
else
  iperf3 -u -c "\${SPARK_IP}" -p "\${IPERF_PORT}" -b "\${IPERF_BITRATE}" -l "\${IPERF_UDP_LENGTH}" -t "\${IPERF_DURATION}" -i 1 \
    >"\${LOG_DIR}/iperf_client.log" 2>&1 &
fi

echo \$! > "\${LOG_DIR}/iperf_client.pid"

echo "[raspi-tx] Started."
echo "[raspi-tx] PIDs:"
cat "\${LOG_DIR}"/*.pid 2>/dev/null || true
EOF_TX

chmod +x /home/${RASPI_USER}/raspi_60ghz_demo/raspi_60ghz_sender.sh

cat > /home/${RASPI_USER}/raspi_60ghz_demo/stop_raspi_60ghz_sender.sh <<'EOF_STOP'
#!/usr/bin/env bash
set -Eeuo pipefail

echo "[raspi-stop] stopping raspi senders..."
pkill -f "gst-launch-1.0.*udpsink" 2>/dev/null || true
pkill -f "iperf3" 2>/dev/null || true
killall -9 iperf3 2>/dev/null || true
rm -f /home/system/raspi_60ghz_demo/logs/*.pid 2>/dev/null || true

echo "[raspi-stop] remaining:"
ps -eo pid,pcpu,pmem,cmd | grep -E "gst-launch|iperf3|x264enc|v4l2src" | grep -v grep || true
EOF_STOP

chmod +x /home/${RASPI_USER}/raspi_60ghz_demo/stop_raspi_60ghz_sender.sh

echo "=== Raspi route to Spark demo ==="
ip route get ${SPARK_DEMO_IP} || true
EOF_RASPI
}

verify_link() {
  log "Verificando rutas y ping por red demo..."

  echo
  echo "=== Spark -> Raspi demo route ==="
  ip route get "${RASPI_DEMO_IP}" || true

  echo
  echo "=== Spark -> Raspi ping ==="
  ping -c 5 -W 2 "${RASPI_DEMO_IP}" || true

  echo
  echo "=== Raspi -> Spark ping ==="
  ssh -o StrictHostKeyChecking=accept-new "${RASPI_USER}@${RASPI_MGMT_IP}" "ping -c 5 -W 2 ${SPARK_DEMO_IP}" || true

  echo
  echo "=== .13 STA -> .12 AP radio ping ==="
  ssh_ant "${STA_HOST}" "ping -I wlan0 -c 5 -W 2 ${AP_WLAN_IP}" || true

  echo
  echo "=== .12 AP -> .13 STA radio ping ==="
  ssh_ant "${AP_HOST}" "ping -I wlan0 -c 5 -W 2 ${STA_WLAN_IP}" || true
}

main() {
  log "Setup Raspi 60GHz link"
  log ".12 AP lado Raspi | .13 STA lado Spark"
  log "Raspi demo=${RASPI_DEMO_IP} | Spark demo=${SPARK_DEMO_IP}"

  configure_spark_ip_routes

  wait_ssh_ant "${AP_HOST}" "AP .12"
  wait_ssh_ant "${STA_HOST}" "STA .13"
  wait_ssh_raspi

  clean_antennas
  configure_ap12
  configure_sta13
  configure_raspi_routes_and_sender
  verify_link

  log "Setup terminado."
  log "Cuando el ping Spark <-> Raspi por 172.16.x funcione, puedes quitar cables intermedios."
}

main "$@"
