# Raspi 60 GHz Pipeline

Main script:

```text
run_raspi_60ghz_pipeline.sh
```

This script launches Spark receivers and starts the sender on the Raspi.

---

## 1. Spark side

- `iperf3` server on `SPARK_IP`.
- MP4 RTP receiver on UDP `6002`.
- Camera RTP receiver + YOLO on UDP `6000`.
- Throughput plot.

---

## 2. Raspi side

Required script:

```text
/home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
```

It can send:

- camera stream;
- MP4 stream;
- `iperf3` traffic.

---

## 3. Run

```bash
cd /home/nextnet/AlbertoDir/go2_dt

RASPI_HOST=172.16.13.100 \
RASPI_USER=nextnet \
SPARK_IP=172.16.12.170 \
ENABLE_CAMERA=1 \
ENABLE_MP4=1 \
ENABLE_IPERF=1 \
./tests_experiments/raspi_60ghz/run_raspi_60ghz_pipeline.sh
```

---

## 4. Custom MP4

On Raspi:

```bash
MP4_FILE=/home/nextnet/raspi_60ghz_demo/my_video.mp4 \
ENABLE_MP4=1 \
/home/system/raspi_60ghz_demo/raspi_60ghz_sender.sh
```
