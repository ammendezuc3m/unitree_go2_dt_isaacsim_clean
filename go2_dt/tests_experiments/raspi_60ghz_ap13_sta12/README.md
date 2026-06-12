# Raspi + AP13/STA12 Pipeline

This folder contains wrapper scripts for the validated Raspi + MikroTik AP13/STA12 architecture.

---

## 1. Default topology

```text
Spark / main PC
  enP7s7
  172.16.12.170
        |
AP13 MikroTik/OpenWrt
  management: 192.168.1.13
  radio wlan0: 10.10.10.1/24
        |
        | 60 GHz
        |
STA12 MikroTik/OpenWrt
  management: 192.168.1.12
  radio wlan0: 10.10.10.2/24
        |
Raspberry Pi
  demo IP: 172.16.13.100
```

---

## 2. Setup

```bash
cd /home/nextnet/AlbertoDir/go2_dt

RASPI_MGMT_HOST=10.39.251.226 \
RASPI_USER=nextnet \
./tests_experiments/raspi_60ghz_ap13_sta12/setup_ap13_sta12_link_raspi_wifi.sh
```

---

## 3. Run

```bash
cd /home/nextnet/AlbertoDir/go2_dt

RASPI_HOST=172.16.13.100 \
RASPI_MGMT_HOST=10.39.251.226 \
SPARK_IP=172.16.12.170 \
STA_MGMT_HOST=192.168.1.12 \
IPERF_MODE=tcp \
IPERF_BITRATE=70M \
ENABLE_CAMERA=1 \
ENABLE_MP4=1 \
ENABLE_IPERF=1 \
ENABLE_CSI=0 \
./tests_experiments/raspi_60ghz_ap13_sta12/run_ap13_sta12_pipeline.sh
```

---

## 4. Stop

```bash
./tests_experiments/raspi_60ghz_ap13_sta12/stop_ap13_sta12_pipeline.sh
```

or:

```bash
./stop_full_demo_current.sh
```

---

## 5. Ports

| Port | Direction | Purpose |
|---:|---|---|
| `6000/udp` | Raspi → Spark | Camera RTP stream. |
| `6002/udp` | Raspi → Spark | MP4 RTP stream. |
| `5201/tcp/udp` | Raspi → Spark | `iperf3`. |
