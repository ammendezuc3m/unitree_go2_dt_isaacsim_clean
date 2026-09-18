# README RASPI

## 1. Objetivo de esta demo

Este proyecto ejecuta una demo distribuida entre **Spark**, **Raspi**, **AP .13**, **STA .12**, **Isaac Sim**, **Go2/ROS2**, **CSI**, **YOLO**, **MP4 golden**, **iperf** y **visualización de throughput**.

La arquitectura actual separa varios flujos:

| Flujo | Origen | Destino | Puerto | Descripción |
|---|---:|---:|---:|---|
| Cámara real / YOLO | Raspi `172.16.13.100` | Spark `172.16.12.170` | UDP `6000` | Cámara Raspi enviada por RTP/H264 y procesada con YOLO en Spark |
| Vídeo golden MP4 | Raspi `172.16.13.100` | Spark `172.16.12.170` | UDP `6002` | Vídeo `golden_test.mp4` enviado en bucle |
| iperf | Raspi `172.16.13.100` | Spark `172.16.12.170` | TCP `5201` | Carga de tráfico configurable |
| CSI | STA `.12` | Spark | fichero local | Stream CSI capturado desde STA |
| Robot / ROS2 / Isaac | Spark | Spark / Docker / Go2 | ROS2 | Simulación, SLAM, patrol y autómata |

Ruta principal:

```text
Spark 172.16.12.170
  -> AP .13
  -> enlace 60 GHz AP .13 / STA .12
  -> Raspi 172.16.13.100
```

---

## 2. Topología lógica

```text
Spark
  IP demo: 172.16.12.170
  Interfaz demo: enP7s7
  Ejecuta:
    - Isaac Sim
    - Go2 SLAM / ROS2
    - YOLO RX
    - MP4 RX
    - iperf server
    - throughput plot
    - CSI predictor
    - zone loop patrol

AP .13
  Gestión: 192.168.1.13
  Radio 60 GHz: 10.10.10.1
  Red lado Spark: 172.16.12.0/24

STA .12
  Gestión vía Raspi WiFi: 192.168.1.12
  Radio 60 GHz: 10.10.10.2
  Red lado Raspi: 172.16.13.0/24

Raspi
  Gestión externa: 163.117.140.252
  IP demo: 172.16.13.100
  Ejecuta:
    - Cámara RTP/H264 a Spark:6000
    - MP4 golden en bucle a Spark:6002
    - iperf client hacia Spark:5201
```

---

## 3. Directorios y scripts principales

### En Spark

```bash
/home/nextnet/AlbertoDir/go2_dt
```

Scripts importantes:

```text
run_full_demo_ap13_sta12_raspi.sh
stop_full_demo_ap13_sta12_raspi_clean.sh

tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh
tests_experiments/raspi_60ghz_ap13_sta12/run_demo_ap13_sta12_no_setup.sh

tests_experiments/raspi_60ghz_ap13_sta12/mp4_rx_stable_6002.sh
tests_experiments/raspi_60ghz/rx_camera_rtp_yolox_realtime_pipe.py

launch_r6_mp4_clean.sh
```

### En Raspi

```bash
/home/system/raspi_60ghz_demo
```

Scripts importantes:

```text
raspi_60ghz_sender.sh
start_mp4_loop_clean.sh
mp4_loop_tx_6002.sh
iperf_client_loop.sh
```

Vídeo golden:

```bash
/home/nextnet/raspi_60ghz_demo/golden_test.mp4
```

Logs Raspi:

```bash
/home/system/raspi_60ghz_demo/logs
```

---

## 4. Flujo normal de ejecución completa

### 4.1. Entrar al proyecto

En Spark:

```bash
cd /home/nextnet/AlbertoDir/go2_dt
```

### 4.2. Parar restos anteriores

```bash
RASPI_MGMT_HOST=163.117.140.252 \
RASPI_USER=nextnet \
./stop_full_demo_ap13_sta12_raspi_clean.sh
```

Comprobación opcional:

```bash
echo "=== Spark restos ==="
ps -ef | grep -Ei "isaac|rx_camera_rtp_yolox|mp4_rx|gst-launch|iperf3|throughput|zone_loop|slam_toolbox|rviz2|launch_r6" | grep -v grep || true

echo
echo "=== Raspi restos ==="
ssh nextnet@172.16.13.100 '
ps -eo pid,ppid,etime,cmd | grep -Ei "start_mp4_loop_clean|mp4_loop_tx_6002|raspi_60ghz_sender|gst-launch|iperf_client_loop|iperf3|v4l2src|filesrc" | grep -v grep || true
'
```

### 4.3. Ejecutar setup de enlace radio/rutas

Este paso configura AP .13, STA .12, rutas y conectividad entre Spark y Raspi.

```bash
RASPI_MGMT_HOST=163.117.140.252 \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh
```

### 4.4. Verificar conectividad antes del run

```bash
echo "=== Spark -> Raspi demo ==="
ip route get 172.16.13.100
ping -c 5 -W 2 172.16.13.100

echo
echo "=== Spark SSH -> Raspi demo ==="
ssh -o ConnectTimeout=5 nextnet@172.16.13.100 '
echo SSH_OK_FROM_SPARK_TO_RASPI
ip route get 172.16.12.170
ping -c 3 -W 2 172.16.12.170
'

echo
echo "=== STA .12 -> AP .13 radio ==="
ssh nextnet@163.117.140.252 '
ssh -oHostKeyAlgorithms=+ssh-rsa \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    root@192.168.1.12 "
iw dev wlan0 link || true
ping -I wlan0 -c 5 -W 2 10.10.10.1 || true
"
'
```

Estado esperado:

```text
Spark -> Raspi demo: 0% packet loss
Spark SSH -> Raspi demo: SSH_OK_FROM_SPARK_TO_RASPI + 0% packet loss
STA .12 -> AP .13 radio: Connected to TEST-LINK + 0% packet loss
```

Si la radio no está asociada, no lanzar la demo.

### 4.5. Ejecutar demo completa

Con los defaults actuales, el run puede hacerse sin argumentos:

```bash
./run_full_demo_ap13_sta12_raspi.sh
```

Si quieres ser explícito:

```bash
RASPI_MGMT_HOST=163.117.140.252 \
RASPI_USER=nextnet \
APRILTAG_CAMERA_DEVICE=/dev/video2 \
ZONE_LOOP_CAMERA_INDEX=2 \
MP4_FILE=/home/nextnet/raspi_60ghz_demo/golden_test.mp4 \
./run_full_demo_ap13_sta12_raspi.sh
```

---

## 5. Ventanas esperadas

Al ejecutar la demo completa deben aparecer ventanas similares a:

```text
T1 - Isaac Sim
T2 - Go2 SLAM Live Visual
T3 - Raspi 60GHz Video YOLO iperf
R1 - iperf server Spark
R2 - Raspi MP4 RX
R6 - Raspi MP4 Loop TX
R5 - CSI Stream STA12
T4 - Throughput Live Plot
T8 - Zone Loop Patrol
```

La ventana importante para el MP4 golden es:

```text
R6 - Raspi MP4 Loop TX
```

Debe permanecer abierta. Dentro debe verse:

```text
[R6-CLEAN] start
[R6-CLEAN] lanzando loop en primer plano...
[MP4-LOOP] start
[MP4-LOOP] launching gst
```

Cuando termine el MP4, debe mostrar:

```text
[MP4-LOOP] gst ended rc=0; restarting in 0.5s
[MP4-LOOP] launching gst
```

---

## 6. Ejecución por partes

### 6.1. Solo setup radio/rutas

```bash
cd /home/nextnet/AlbertoDir/go2_dt

RASPI_MGMT_HOST=163.117.140.252 \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh
```

### 6.2. Solo comprobar radio AP/STA

```bash
ssh nextnet@163.117.140.252 '
ssh -oHostKeyAlgorithms=+ssh-rsa \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    root@192.168.1.12 "
iw dev wlan0 link || true
ping -I wlan0 -c 5 -W 2 10.10.10.1 || true
"
'
```

Si aparece:

```text
Not connected.
wlan0: <NO-CARRIER>
```

el problema está en la asociación 60 GHz AP .13 ↔ STA .12.

### 6.3. Reinicio limpio solo de radio AP/STA

Cuando el setup deja AP/STA sin asociación, reiniciar solo radio:

```bash
echo "=== limpia AP .13 ==="
ssh -oHostKeyAlgorithms=+ssh-rsa \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    root@192.168.1.13 '
killall -9 hostapd 2>/dev/null || true
killall -9 wpa_supplicant 2>/dev/null || true
ip link set wlan0 down 2>/dev/null || true
sleep 2
ip link set wlan0 up
sleep 2
ip addr flush dev wlan0
ip addr add 10.10.10.1/24 dev wlan0
hostapd -B /tmp/hostapd_ap13_60g.conf
sleep 3
ip -br a
ps w | grep hostapd | grep -v grep
'

echo
echo "=== limpia STA .12 ==="
ssh nextnet@163.117.140.252 '
ssh -oHostKeyAlgorithms=+ssh-rsa \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    root@192.168.1.12 "
killall -9 wpa_supplicant 2>/dev/null || true
killall -9 hostapd 2>/dev/null || true
rm -f /var/run/wpa_supplicant/wlan0 2>/dev/null || true
ip link set wlan0 down 2>/dev/null || true
sleep 2
ip link set wlan0 up
sleep 2
ip addr flush dev wlan0
ip addr add 10.10.10.2/24 dev wlan0
wpa_supplicant -D nl80211 -i wlan0 -c /tmp/wpa_sta12_60g.conf -B
sleep 8
iw dev wlan0 link || true
ip -br a
"
'
```

Reparar rutas si la radio ya conecta:

```bash
echo "=== rutas AP ==="
ssh -oHostKeyAlgorithms=+ssh-rsa \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    root@192.168.1.13 '
echo 1 > /proc/sys/net/ipv4/ip_forward
ip route replace 172.16.13.0/24 via 10.10.10.2 dev wlan0
ip route
'

echo
echo "=== rutas STA ==="
ssh nextnet@163.117.140.252 '
ssh -oHostKeyAlgorithms=+ssh-rsa \
    -oPubkeyAcceptedAlgorithms=+ssh-rsa \
    root@192.168.1.12 "
echo 1 > /proc/sys/net/ipv4/ip_forward
ip route replace 172.16.12.0/24 via 10.10.10.1 dev wlan0
ip route
"
'
```

### 6.4. Solo sender MP4 golden en Raspi

Lanza el TX MP4 limpio en ventana R6:

```bash
cd /home/nextnet/AlbertoDir/go2_dt

RASPI_HOST=172.16.13.100 \
SPARK_IP=172.16.12.170 \
MP4_FILE=/home/nextnet/raspi_60ghz_demo/golden_test.mp4 \
MP4_PORT=6002 \
./launch_r6_mp4_clean.sh
```

Comprobar TX en Raspi:

```bash
ssh nextnet@172.16.13.100 '
ps -eo pid,ppid,etime,cmd | grep -E "start_mp4_loop_clean|mp4_loop_tx_6002|gst-launch-1.0.*filesrc" | grep -v grep || true
'
```

Comprobar tráfico en Spark:

```bash
timeout 5 sudo tcpdump -ni enP7s7 "udp port 6002" -c 20
```

### 6.5. Solo receptor MP4 golden en Spark

Receptor estable, sin reinicios:

```bash
SPARK_IP=172.16.12.170 \
MP4_PORT=6002 \
/home/nextnet/AlbertoDir/go2_dt/tests_experiments/raspi_60ghz_ap13_sta12/mp4_rx_stable_6002.sh
```

Comprobar RX:

```bash
ps -eo pid,ppid,etime,cmd | grep -E "mp4_rx_stable_6002|gst-launch-1.0.*6002|udpsrc.*6002" | grep -v grep || true
```

### 6.6. Solo cámara Raspi hacia YOLO en Spark

El flujo de cámara va por UDP `6000`.

Comprobar que llega:

```bash
timeout 5 sudo tcpdump -ni enP7s7 "udp port 6000" -c 20
```

Probar decodificación sin YOLO:

```bash
sudo fuser -k 6000/udp 2>/dev/null || true

gst-launch-1.0 -v \
  udpsrc port=6000 \
  caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" ! \
  rtpjitterbuffer latency=100 drop-on-latency=true ! \
  rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! \
  fpsdisplaysink video-sink=autovideosink sync=false text-overlay=true
```

Lanzar receptor YOLO real:

```bash
cd /home/nextnet/AlbertoDir/go2_dt

pkill -TERM -f "rx_camera_rtp_yolox" 2>/dev/null || true
sleep 1
pkill -KILL -f "rx_camera_rtp_yolox" 2>/dev/null || true
sudo fuser -k 6000/udp 2>/dev/null || true

./venvs/webcam_yolo_env/bin/python \
  /home/nextnet/AlbertoDir/go2_dt/tests_experiments/raspi_60ghz/rx_camera_rtp_yolox_realtime_pipe.py \
  --rx-port 6000 \
  --latency-ms 80 \
  --width 424 \
  --height 240 \
  --fps 15 \
  --camera-json-out /home/nextnet/AlbertoDir/go2_dt/camera_yolo/outputs/live_camera_state.json \
  --yolo-exp-file /home/nextnet/AlbertoDir/go2_dt/camera_yolo/YOLOX/exps/default/yolox_s.py \
  --yolo-ckpt /home/nextnet/AlbertoDir/go2_dt/camera_yolo/Weights/yolox_s.pth \
  --yolo-device gpu \
  --yolo-tsize 256 \
  --yolo-process-fps 5 \
  --yolo-conf 0.25 \
  --yolo-nms 0.45 \
  --yolo-person-min-score 0.55 \
  --yolo-person-min-height-ratio 0.35 \
  --yolo-person-min-area-ratio 0.04 \
  --yolo-person-min-aspect-ratio 1.15 \
  --yolo-fp16 \
  --verbose
```

### 6.7. Solo iperf

Servidor en Spark:

```bash
iperf3 -s -B 172.16.12.170 -p 5201 -i 1
```

Cliente loop en Raspi:

```bash
ssh nextnet@172.16.13.100 '
SPARK_IP=172.16.12.170 \
IPERF_PORT=5201 \
IPERF_MODE=tcp \
IPERF_BITRATE=400M \
IPERF_DURATION=3600 \
/home/system/raspi_60ghz_demo/iperf_client_loop.sh
'
```

Comprobar proceso:

```bash
ssh nextnet@172.16.13.100 '
ps -eo pid,ppid,etime,cmd | grep -E "iperf_client_loop|iperf3" | grep -v grep || true
'
```

Comprobar tráfico:

```bash
timeout 5 sudo tcpdump -ni enP7s7 "tcp port 5201" -c 20
```

### 6.8. Solo throughput plot

```bash
cd /home/nextnet/AlbertoDir/go2_dt

python3 tests_experiments/raspi_60ghz_ap13_sta12/throughput_local_rx_tk_smooth.py \
  --local-iface enP7s7 \
  --sta-host 192.168.1.12 \
  --scale-max 500 \
  --samples 240 \
  --interval 1 \
  --smooth-samples 30 \
  --ema-alpha 0.08 \
  --max-step-mbps 8
```

### 6.9. Solo CSI stream

```bash
cd /home/nextnet/AlbertoDir/go2_dt

mkdir -p csi_dog_dataset_20210421_181125/realtime_inputs
: > csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt

ssh -o ConnectTimeout=8 nextnet@163.117.140.252 \
  "ssh -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa root@192.168.1.12 '/root/scripts_csi_dog/stream_csi_stdout_05s_ap13.sh'" \
  | tee -a csi_dog_dataset_20210421_181125/realtime_inputs/live_csi_stream.txt
```

### 6.10. Solo comprobar JSON YOLO

```bash
watch -n 0.5 'cat /home/nextnet/AlbertoDir/go2_dt/camera_yolo/outputs/live_camera_state.json 2>/dev/null | jq .'
```

Sin `jq`:

```bash
watch -n 0.5 'cat /home/nextnet/AlbertoDir/go2_dt/camera_yolo/outputs/live_camera_state.json 2>/dev/null'
```

---

## 7. Cambiar bitrate iperf por código

Si quieres que el run simple use otro bitrate sin pasar variables, cambia los defaults:

### En Spark

```bash
nano /home/nextnet/AlbertoDir/go2_dt/run_full_demo_ap13_sta12_raspi.sh
```

Buscar:

```bash
IPERF_BITRATE="${IPERF_BITRATE:-400M}"
```

Cambiar por, por ejemplo:

```bash
IPERF_BITRATE="${IPERF_BITRATE:-100M}"
```

También cambiar en:

```bash
nano /home/nextnet/AlbertoDir/go2_dt/tests_experiments/raspi_60ghz_ap13_sta12/run_demo_ap13_sta12_no_setup.sh
```

Buscar:

```bash
IPERF_BITRATE="${IPERF_BITRATE:-400M}"
```

### En Raspi

```bash
ssh nextnet@172.16.13.100
nano /home/system/raspi_60ghz_demo/iperf_client_loop.sh
```

Buscar:

```bash
IPERF_BITRATE="${IPERF_BITRATE:-400M}"
```

Y opcionalmente también:

```bash
nano /home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
```

Buscar:

```bash
IPERF_BITRATE="${IPERF_BITRATE:-400M}"
```

Verificación:

```bash
grep -nE "IPERF_MODE|IPERF_BITRATE" \
  /home/nextnet/AlbertoDir/go2_dt/run_full_demo_ap13_sta12_raspi.sh \
  /home/nextnet/AlbertoDir/go2_dt/tests_experiments/raspi_60ghz_ap13_sta12/run_demo_ap13_sta12_no_setup.sh

ssh nextnet@172.16.13.100 \
  "grep -nE 'IPERF_MODE|IPERF_BITRATE' /home/system/raspi_60ghz_demo/iperf_client_loop.sh /home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh"
```

---

## 8. Verificaciones rápidas durante la demo

### Procesos Raspi

```bash
ssh nextnet@172.16.13.100 '
ps -eo pid,ppid,etime,cmd | grep -E "v4l2src|start_mp4_loop_clean|mp4_loop_tx_6002|gst-launch-1.0.*filesrc|iperf_client_loop|iperf3" | grep -v grep || true
'
```

### Procesos Spark

```bash
ps -eo pid,ppid,etime,cmd | grep -E "mp4_rx_stable_6002|gst-launch-1.0.*6002|rx_camera_rtp_yolox|iperf3 -s|throughput|zone_loop" | grep -v grep || true
```

### Tráfico cámara

```bash
timeout 3 sudo tcpdump -ni enP7s7 "udp port 6000" -c 5
```

### Tráfico MP4

```bash
timeout 3 sudo tcpdump -ni enP7s7 "udp port 6002" -c 5
```

### Tráfico iperf TCP

```bash
timeout 3 sudo tcpdump -ni enP7s7 "tcp port 5201" -c 5
```

### Log MP4 loop

```bash
ssh nextnet@172.16.13.100 '
tail -f /home/system/raspi_60ghz_demo/logs/mp4_loop_tx_6002.log
'
```

### Log iperf

```bash
ssh nextnet@172.16.13.100 '
tail -f /home/system/raspi_60ghz_demo/logs/iperf_client.log
'
```

---

## 9. Errores típicos y diagnóstico

### 9.1. YOLO funciona pero golden no aparece

Cámara YOLO y golden son flujos distintos:

```text
YOLO/cámara: UDP 6000
golden MP4: UDP 6002
```

Comprobar MP4:

```bash
ssh nextnet@172.16.13.100 '
ps -eo pid,ppid,etime,cmd | grep -E "start_mp4_loop_clean|mp4_loop_tx_6002|gst-launch-1.0.*filesrc" | grep -v grep || true
'

timeout 5 sudo tcpdump -ni enP7s7 "udp port 6002" -c 20
```

Si no hay paquetes en `6002`, falla TX MP4.

### 9.2. Hay paquetes en 6002 pero no hay ventana

Comprobar RX:

```bash
ps -eo pid,ppid,etime,cmd | grep -E "mp4_rx_stable_6002|gst-launch-1.0.*6002|udpsrc.*6002" | grep -v grep || true
```

Lanzar RX manual:

```bash
SPARK_IP=172.16.12.170 \
MP4_PORT=6002 \
/home/nextnet/AlbertoDir/go2_dt/tests_experiments/raspi_60ghz_ap13_sta12/mp4_rx_stable_6002.sh
```

### 9.3. Setup no asocia STA .12 al AP .13

Síntomas:

```text
Not connected.
wlan0: <NO-CARRIER>
Destination Host Unreachable
100% packet loss entre 10.10.10.1 y 10.10.10.2
```

Solución: reiniciar solo AP/STA y reparar rutas, como en la sección 6.3.

### 9.4. iperf se para a los 3600 s

El cliente se ejecuta dentro de:

```bash
/home/system/raspi_60ghz_demo/iperf_client_loop.sh
```

Cuando `iperf3 -t 3600` termina, el script debe relanzarlo automáticamente:

```text
[IPERF-LOOP] iperf ended rc=0; restarting in 1s
[IPERF-LOOP] launching iperf3
```

Ver log:

```bash
ssh nextnet@172.16.13.100 '
tail -n 80 /home/system/raspi_60ghz_demo/logs/iperf_client.log
'
```

### 9.5. Isaac Sim no se cierra con stop

El `stop_full_demo_ap13_sta12_raspi_clean.sh` debe eliminar los contenedores:

```bash
docker rm -f isaac-sim-gui 2>/dev/null || true
docker rm -f isaac-sim 2>/dev/null || true
```

Verificar:

```bash
docker ps --format '{{.ID}} {{.Names}} {{.Image}}' | grep -Ei "isaac|omniverse|nvcr.io/nvidia/isaac" || true
```

---

## 10. Stop completo

```bash
cd /home/nextnet/AlbertoDir/go2_dt

RASPI_MGMT_HOST=163.117.140.252 \
RASPI_USER=nextnet \
./stop_full_demo_ap13_sta12_raspi_clean.sh
```

Verificar:

```bash
echo "=== Spark restos ==="
ps -ef | grep -Ei "isaac|rx_camera_rtp_yolox|mp4_rx|gst-launch|iperf3|throughput|zone_loop|slam_toolbox|rviz2|launch_r6" | grep -v grep || true

echo
echo "=== Docker restos ==="
docker ps --format '{{.ID}} {{.Names}} {{.Image}}' | grep -Ei "isaac|omniverse|nvcr.io/nvidia/isaac" || true

echo
echo "=== Raspi restos ==="
ssh nextnet@172.16.13.100 '
ps -eo pid,ppid,etime,cmd | grep -Ei "start_mp4_loop_clean|mp4_loop_tx_6002|raspi_60ghz_sender|gst-launch|iperf_client_loop|iperf3|v4l2src|filesrc" | grep -v grep || true
'
```

---

## 11. Resumen operativo

Para ejecución completa normal:

```bash
cd /home/nextnet/AlbertoDir/go2_dt

RASPI_MGMT_HOST=163.117.140.252 \
RASPI_USER=nextnet \
./stop_full_demo_ap13_sta12_raspi_clean.sh

RASPI_MGMT_HOST=163.117.140.252 \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh

./run_full_demo_ap13_sta12_raspi.sh
```

Para ejecución rápida si ya sabes que la radio/rutas están bien:

```bash
cd /home/nextnet/AlbertoDir/go2_dt
./run_full_demo_ap13_sta12_raspi.sh
```
