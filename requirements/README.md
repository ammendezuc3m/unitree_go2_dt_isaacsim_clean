# Requirements

Use separate environments where possible.

## System packages

```bash
sudo apt update
sudo apt install -y \
  python3-pip python3-venv python3-colcon-common-extensions \
  python3-rosdep git curl unzip \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav \
  iperf3 tcpdump v4l-utils xterm
```

## Host utilities

```bash
python3 -m venv .venv_host
source .venv_host/bin/activate
pip install --upgrade pip
pip install -r requirements/requirements-host.txt
```

## CSI

```bash
python3 -m venv .venv_csi
source .venv_csi/bin/activate
pip install --upgrade pip
pip install -r requirements/requirements-csi.txt
```

## YOLO

```bash
python3 -m venv .venv_yolo
source .venv_yolo/bin/activate
pip install --upgrade pip
pip install -r requirements/requirements-yolo.txt
```

ROS 2 nodes should normally run from the ROS 2 sourced system environment, not from a random venv.
