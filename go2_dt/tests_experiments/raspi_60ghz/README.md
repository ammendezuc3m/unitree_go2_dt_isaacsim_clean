# Raspi 60 GHz Pipeline

This folder contains the Spark-side launcher for the Raspi sender architecture.

Main script:

```text
run_raspi_60ghz_pipeline.sh
```

This script starts Spark-side receivers and then starts the sender installed on the Raspberry Pi.

---

## 1. Required deployment scripts

Before running this pipeline, install the Raspi-side scripts from the repository:

```text
deployment/raspi/raspi_60ghz_demo/
├── raspi_60ghz_sender.sh
└── iperf_client_loop.sh
```

Target path on the Raspberry Pi:

```text
~/raspi_60ghz_demo/
```

Install from the PC/Spark:

```bash
cd <repo-root>

ssh ${RASPI_USER:-nextnet}@172.16.13.100 'sudo mkdir -p ~/raspi_60ghz_demo && sudo chown -R nextnet:nextnet ~/raspi_60ghz_demo'

scp deployment/raspi/raspi_60ghz_demo/*.sh \
  ${RASPI_USER:-nextnet}@172.16.13.100:~/raspi_60ghz_demo/

ssh ${RASPI_USER:-nextnet}@172.16.13.100 'chmod +x ~/raspi_60ghz_demo/*.sh'
```

For full deployment details, see:

```text
deployment/README.md
docs/mikrotik_csi_openwrt.md
```

---

## 2. What the pipeline does

### Spark side

- Starts `iperf3` server on `SPARK_IP`.
- Starts MP4 RTP receiver on UDP `6002`.
- Starts camera RTP receiver + YOLO pipeline on UDP `6000`.
- Starts throughput plot if enabled.

### Raspi side

- Starts camera sender.
- Starts optional MP4 sender.
- Starts persistent `iperf3` client loop.

---

## 3. Run

```bash
cd go2_dt

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

The Raspi sender uses:

```text
MP4_FILE=~/raspi_60ghz_demo/golden_test.mp4
```

To use another file, copy it to the Raspi:

```bash
scp my_video.mp4 ${RASPI_USER:-nextnet}@172.16.13.100:~/raspi_60ghz_demo/my_video.mp4
```

Then run:

```bash
MP4_FILE=~/raspi_60ghz_demo/my_video.mp4 \
ENABLE_MP4=1 \
~/raspi_60ghz_demo/raspi_60ghz_sender.sh
```

If only `MP4_FILE` changes, the Spark receiver does not need to change. If the RTP port changes, update both the Raspi sender and the Spark receiver.

---

## 5. Check logs

On the Raspi:

```bash
ls -lh ~/raspi_60ghz_demo/logs
tail -f ~/raspi_60ghz_demo/logs/camera_tx.log
tail -f ~/raspi_60ghz_demo/logs/mp4_tx.log
tail -f ~/raspi_60ghz_demo/logs/iperf_client.log
```

On the PC/Spark:

```bash
sudo tcpdump -i enP7s7 udp port 6000
sudo tcpdump -i enP7s7 udp port 6002
```
