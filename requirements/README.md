# Requirements

The supported host setup is **Ubuntu 24.04 + ROS 2 Jazzy**. ROS 2 Jazzy is the ROS release used by this repository.

The recommended configuration is one host virtual environment for all non-Isaac Python code. It is created with `--system-site-packages` so ROS 2 packages installed by apt (for example `rclpy`) remain visible.

## 1. System packages

Install ROS 2 Jazzy first, then install the common tools used by the demo:

```bash
sudo apt update
sudo apt install -y \
  git git-lfs curl unzip \
  python3-pip python3-venv python3-colcon-common-extensions python3-rosdep \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav \
  iperf3 tcpdump v4l-utils xterm \
  ros-jazzy-rmw-cyclonedds-cpp \
  ros-jazzy-slam-toolbox \
  ros-jazzy-pointcloud-to-laserscan \
  ros-jazzy-robot-state-publisher
```

The ROS workspace has additional package dependencies. Install them through rosdep after cloning:

```bash
source /opt/ros/jazzy/setup.bash
sudo rosdep init 2>/dev/null || true
rosdep update
rosdep install --from-paths go2_dt/ros2_ws/src --ignore-src -r -y
```

## 2. Git LFS

Large runtime files are tracked with Git LFS, including Isaac USD assets, YOLO weights, the CSI model and the demo MP4.

```bash
git lfs install
git lfs pull
```

Do not continue until the runtime files are real files rather than small Git LFS pointer text files.

Useful check:

```bash
git lfs ls-files
ls -lh \
  go2_dt/camera_yolo/Weights/yolox_s.pth \
  go2_dt/csi_dog_dataset_20210421_181125/analysis_outputs/csi_empty_person_model_20260526_all_1_2200.joblib \
  go2_dt/media/golden_test.mp4
```

## 3. Unified Python environment

From the repository root:

```bash
python3 -m venv --system-site-packages go2_dt/.venv
go2_dt/.venv/bin/pip install --upgrade pip wheel setuptools
go2_dt/.venv/bin/pip install -r requirements/requirements-runtime.txt
```

Why `--system-site-packages` is intentional:

- ROS 2 Jazzy installs `rclpy`, message packages and other bindings through apt.
- the demo also needs PyTorch/YOLOX, CSI/scikit-learn and Go2 WebRTC Python dependencies;
- one environment avoids the previous mix of `.venv_yolo`, `venvs/webcam_yolo_env` and `camera_yolo/yolox`.

The active launchers now expect:

```text
go2_dt/.venv/bin/python
```

You can override it with:

```bash
export PYTHON_BIN=/path/to/python
```

## 4. Build the ROS 2 workspace

```bash
cd go2_dt/ros2_ws
source /opt/ros/jazzy/setup.bash
source ../.venv/bin/activate

colcon build --symlink-install
source install/setup.bash
```

Quick checks:

```bash
python -c "import rclpy, cv2, torch, aiortc, sklearn; print('python deps OK')"
ros2 pkg list | grep -E 'go2_robot_sdk|go2_dt_bridge'
```

## 5. Isaac Sim

Isaac Sim runs in its NVIDIA container and does not use the host virtual environment. The validated image is:

```text
nvcr.io/nvidia/isaac-sim:5.1.0
```

Docker, NVIDIA Container Toolkit and a working NVIDIA GPU are required.

## 6. Detailed device deployment

Installing the host dependencies is only one part of the setup. The MikroTik/OpenWrt and Raspberry Pi runtime scripts must also be copied to those devices before the Raspi/full-radio demo can run.

See:

```text
docs/setup_from_scratch.md
deployment/README.md
```
