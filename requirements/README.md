# Requirements

The supported host/Spark setup uses **one project virtual environment** at the repository root:

```text
<repo>/.venv
```

ROS 2 Jazzy itself remains a system installation from APT. The project venv is created with `--system-site-packages` so it can use `rclpy`, `cv_bridge`, `tf2_ros` and the other ROS Python packages installed under `/opt/ros/jazzy`.

Do **not** create/copy a prebuilt venv between machines: Python venvs are not portable. Recreate it from `requirements/requirements-host-all.txt` with the setup script below.

## 1. System / ROS packages

Ubuntu 24.04 + ROS 2 Jazzy is the validated target.

Install the generic host tools:

```bash
sudo apt update
sudo apt install -y \
  python3-pip python3-venv python3-colcon-common-extensions \
  python3-rosdep python3-tk git git-lfs curl unzip openssh-client \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav \
  iperf3 tcpdump v4l-utils xterm gnome-terminal \
  iproute2 iputils-ping psmisc x11-xserver-utils
```

Install ROS 2 Jazzy using the official ROS 2 Ubuntu installation procedure, then install the runtime packages used directly by the current launch path:

```bash
sudo apt install -y \
  ros-jazzy-rmw-cyclonedds-cpp \
  ros-jazzy-robot-state-publisher \
  ros-jazzy-pointcloud-to-laserscan \
  ros-jazzy-slam-toolbox \
  ros-jazzy-cv-bridge \
  ros-jazzy-tf2-ros \
  ros-jazzy-tf2-sensor-msgs
```

The ROS packages declared by the workspace are resolved with `rosdep` during the build step.

## 2. Fetch Git LFS assets

This repository stores the large runtime artifacts with Git LFS. Before building or launching the demo, materialize them:

```bash
git lfs install
git lfs pull
bash requirements/check_lfs_assets.sh
```

The validation script currently checks the runtime-critical files used by the documented demo:

```text
go2_dt/camera_yolo/Weights/yolox_s.pth
go2_dt/csi_dog_dataset_20210421_181125/analysis_outputs/csi_empty_person_model_20260526_all_1_2200.joblib
go2_dt/media/golden_test.mp4
go2_assets/go2/prueba_demo2_new_scenario.usd
```

If any of those files still begins with `version https://git-lfs.github.com/spec/v1`, the repository contains only the pointer and the demo is not ready.

`requirements/setup_host_env.sh` also runs `git lfs pull` and this validation automatically, so the explicit commands above are mainly useful for diagnosis.

## 3. Create the single project venv

From the repository root:

```bash
bash requirements/setup_host_env.sh
```

This creates:

```text
.venv/
```

and installs `requirements/requirements-host-all.txt`, which contains the host utilities, CSI stack, Go2 WebRTC SDK dependencies and YOLOX runtime dependencies.

Important details:

- `numpy==1.26.4` is pinned for compatibility with the current ROS 2 Jazzy / Ubuntu 24.04 stack.
- `opencv-contrib-python` is used instead of `opencv-python` because `zone_loop_patrol_v3.py` requires `cv2.aruco` for the AprilTag 36h11 detector.
- `cryptography` and `requests` are explicit because the included Go2 WebRTC SDK imports them directly.
- `open3d` is **not** part of the canonical environment because the current `go2_slam_live_visual.launch.py` path does not require it. The SDK's legacy/full requirements file still documents it for old paths.

The setup script finishes by importing the critical modules and checking that `cv2.aruco` exists. A failed validation means the environment is not ready.

## 4. Activate the environment

For an interactive terminal:

```bash
source /opt/ros/jazzy/setup.bash
source .venv/bin/activate
```

Then verify:

```bash
python -c 'import rclpy, cv2, aiortc, torch, sklearn; print("Python stack OK", cv2.__version__)'
```

## 5. Build the ROS 2 workspace

Keep the same venv active while building:

```bash
source /opt/ros/jazzy/setup.bash
source .venv/bin/activate

cd go2_dt/ros2_ws

sudo rosdep init 2>/dev/null || true
rosdep update
rosdep install --from-paths src --ignore-src -r -y

colcon build --symlink-install
source install/setup.bash
```

Building while `.venv` is active ensures the Python ROS packages in this workspace use the same interpreter/dependency set as the demo.

## 6. Component requirement files

The older component files are kept for debugging or isolated experiments:

```text
requirements-host.txt
requirements-csi.txt
requirements-yolo.txt
go2_dt/ros2_ws/src/go2_ros2_sdk/requirements*.txt
```

They are **not the recommended installation path for the full demo**. For a clean machine use `requirements/setup_host_env.sh` plus `requirements-host-all.txt`.

## 7. Isaac Sim

Isaac Sim runs in its NVIDIA Docker container and therefore has its own Python runtime. The host `.venv` is for the Spark/PC processes (ROS 2, Go2 SDK, CSI, YOLO, utilities); it is not copied into the Isaac container.
