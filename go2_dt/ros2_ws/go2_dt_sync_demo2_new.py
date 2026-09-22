import threading
import math
import builtins
import json
import os
import time
from pathlib import Path

import omni.usd
import omni.kit.app
import omni.kit.commands

from pxr import UsdGeom, Gf, UsdPhysics, PhysxSchema, Vt
from isaacsim.sensors.physx import _range_sensor

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from geometry_msgs.msg import PoseStamped
from std_msgs.msg import Float32MultiArray
from sensor_msgs.msg import PointCloud2
from sensor_msgs_py import point_cloud2


# ============================================================
# MAIN FEATURE FLAGS
# ============================================================

ENABLE_SIMULATED_LIDAR = True
ENABLE_REAL_LIDAR_CLOUD = True
ENABLE_REAL_CLOUD_LOGS = True

ENABLE_MMWAVE_PERSON_VISIBILITY = True
ENABLE_SENSOR_PERSON_VISIBILITY = True

# Lee live_patrol_state.json y, cuando detecta start_pose_event nuevo,
# recalibra el anchor real->Isaac.
ENABLE_PATROL_ANCHOR_RESET = True

# The host repository is mounted into the Isaac container by the launcher.
# /workspace is an internal container mount point, not a host filesystem path.
WORKSPACE_ROOT = Path(os.environ.get("GO2_WORKSPACE_ROOT", "/workspace")).resolve()
GO2_ROOT = WORKSPACE_ROOT / "go2_dt"



# ============================================================
# FIXED START POSE IN ISAAC
# ============================================================

FIXED_START_ISAAC_X = 2.94919
FIXED_START_ISAAC_Y = 3.74258
FIXED_START_ISAAC_YAW_DEG = -153.321

USE_FIXED_START_ANCHOR = True

# Escala entre desplazamiento físico real y desplazamiento visual en Isaac.
REAL_TO_ISAAC_SCALE_X = 1.25
REAL_TO_ISAAC_SCALE_Y = 1.25


# ============================================================
# PATROL / APRILTAG ANCHOR RESET CONFIG
# ============================================================

PATROL_STATE_JSON_PATH = str(GO2_ROOT / "ros2_ws" / "patrol_outputs" / "live_patrol_state.json")

PATROL_STATE_JSON_FALLBACK_PATHS = [
    PATROL_STATE_JSON_PATH,
]

PATROL_POLL_INTERVAL_SEC = 0.10
PATROL_STATE_MAX_AGE_SEC = 5.0

# Tras un reset se desactiva smoothing durante varios frames.
ANCHOR_RESET_WARMUP_FRAMES = 10

# Frames congelados explícitamente en pose fija justo después del reset.
# Esto evita que un frame viejo arrastre visualmente el perro.
ANCHOR_RESET_FREEZE_FRAMES = 2

PATROL_ANCHOR_DEBUG_LOGS = True


# ============================================================
# mmWave / CSI PREDICTION CONFIG
# ============================================================

MMWAVE_STATE_JSON_PATH = str(GO2_ROOT / "csi_dog_dataset_20210421_181125" / "analysis_outputs" / "live_prediction_state.json")

MMWAVE_STATE_JSON_FALLBACK_PATHS = [
    MMWAVE_STATE_JSON_PATH,
]

MMWAVE_PERSON_PRIM_PATH = "/World/person_1"

MMWAVE_PERSON_LABELS = {"person", "person_1", "human", "occupied", "true"}
MMWAVE_EMPTY_LABELS = {"empty", "nothing", "none", "clear", "background", "no_object", "no_novel_object", "false"}

MMWAVE_POLL_INTERVAL_SEC = 0.2
MMWAVE_STATE_MAX_AGE_SEC = 2.0

MMWAVE_HIDE_ON_ERROR = True
MMWAVE_HIDE_WHEN_STALE = True


# ============================================================
# OTHER SENSOR JSONS
# ============================================================

LIDAR_STATE_JSON_PATH = str(GO2_ROOT / "ros2_ws" / "lidar_outputs" / "live_lidar_state.json")
LIDAR_PERSON_PRIM_PATH = "/World/person_02"

CAMERA_STATE_JSON_PATH = str(GO2_ROOT / "camera_yolo" / "outputs" / "live_camera_state.json")
CAMERA_PERSON_PRIM_PATH = "/World/person_03"

SENSOR_STATE_MAX_AGE_SEC = 2.0
SENSOR_HIDE_ON_ERROR = True
SENSOR_HIDE_WHEN_STALE = True


# ============================================================
# CONFIG
# ============================================================

ROBOT_XFORM_PATH = "/World/go2"
ARTICULATION_ROOT_PATH = "/World/go2/map"
JOINT_SCOPE = f"{ROBOT_XFORM_PATH}/joints"

JOINT_PATHS = {
    "FL_hip":   f"{JOINT_SCOPE}/FL_hip_joint",
    "FL_thigh": f"{JOINT_SCOPE}/FL_thigh_joint",
    "FL_calf":  f"{JOINT_SCOPE}/FL_calf_joint",

    "FR_hip":   f"{JOINT_SCOPE}/FR_hip_joint",
    "FR_thigh": f"{JOINT_SCOPE}/FR_thigh_joint",
    "FR_calf":  f"{JOINT_SCOPE}/FR_calf_joint",

    "RL_hip":   f"{JOINT_SCOPE}/RL_hip_joint",
    "RL_thigh": f"{JOINT_SCOPE}/RL_thigh_joint",
    "RL_calf":  f"{JOINT_SCOPE}/RL_calf_joint",

    "RR_hip":   f"{JOINT_SCOPE}/RR_hip_joint",
    "RR_thigh": f"{JOINT_SCOPE}/RR_thigh_joint",
    "RR_calf":  f"{JOINT_SCOPE}/RR_calf_joint",
}

HIP_ORIGINS = {
    "FL": (0.1934, -0.0465, 0.0),
    "FR": (0.1934,  0.0465, 0.0),
    "RL": (-0.1934, -0.0465, 0.0),
    "RR": (-0.1934,  0.0465, 0.0),
}

THIGH_LATERAL = {
    "FL": -0.0955,
    "FR":  0.0955,
    "RL": -0.0955,
    "RR":  0.0955,
}

UPPER_LEG = 0.213
LOWER_LEG = 0.213

DRIVE_STIFFNESS = 300.0
DRIVE_DAMPING = 60.0
DRIVE_MAX_FORCE = 50000.0

FORCE_BASE_Z_TO_ZERO = True
BASE_Z_VALUE = 0.0
BASE_Z_OFFSET = 0.0

NEGATIVE_KNEE = True
WARMUP_FRAMES = 20
POSE_SMOOTHING_ALPHA = 0.15


# ============================================================
# GLOBAL STATE
# ============================================================

latest_pose = None
latest_feet = None
latest_real_cloud = None
latest_real_cloud_frame = None

ros_node = None
ros_thread = None
update_sub = None

initialized_once = False
warmup_counter = WARMUP_FRAMES

current_base_pos = None
current_base_quat = None
current_sim_yaw_rad = None

current_raw_world_pos = None
current_raw_world_quat = None

real_cloud_msg_count = 0
real_cloud_apply_count = 0


# ============================================================
# SESSION ANCHOR
# ============================================================

session_anchor_ready = False

session_real_anchor_x = None
session_real_anchor_y = None
session_real_anchor_z = None
session_real_anchor_yaw_deg = None

# Este es el valor clave:
# sim_yaw = real_yaw + session_yaw_offset_deg
# Si AprilTag confirma que real_yaw=90 equivale al inicio fijo,
# entonces offset = FIXED_START_ISAAC_YAW_DEG - 90.
session_yaw_offset_deg = 0.0

anchor_reset_freeze_frames_left = 0


# ============================================================
# JSON STATE RUNTIME
# ============================================================

mmwave_last_poll_monotonic = 0.0
mmwave_last_file_mtime = None
mmwave_last_state = None
mmwave_last_visibility = {}

sensor_last_poll_monotonic = 0.0
sensor_last_file_mtime = {}
sensor_last_state = {}
sensor_last_visibility = {}

patrol_last_poll_monotonic = 0.0
patrol_last_file_mtime = None
patrol_last_state = None
patrol_last_processed_event_id = 0
patrol_last_reset_monotonic = 0.0


# ============================================================
# LIDAR VISUAL / SENSOR CONFIG
# ============================================================

LIDAR_PARENT = "/World/go2/base_link/radar"

LIDAR_BODY_PATH = f"{LIDAR_PARENT}/head_lidar_body"
LIDAR_BODY_RADIUS = 0.035
LIDAR_BODY_HEIGHT = 0.03
LIDAR_BODY_Z = 0.06

LIDAR_PRIM_NAME = "head_lidar"
LIDAR_SENSOR_PATH = f"{LIDAR_PARENT}/{LIDAR_PRIM_NAME}"
LIDAR_SENSOR_Z = 0.075

LIDAR_MIN_RANGE = 0.05
LIDAR_MAX_RANGE = 30.0
LIDAR_HORIZONTAL_FOV = 360.0
LIDAR_VERTICAL_FOV = 90.0
LIDAR_HORIZONTAL_RESOLUTION = 2.0
LIDAR_VERTICAL_RESOLUTION = 2.0
LIDAR_ROTATION_RATE = 20.0

DRAW_LIDAR_POINTS = True
DRAW_LIDAR_LINES = False


# ============================================================
# REAL CLOUD CONFIG
# ============================================================

REAL_CLOUD_TOPIC = "/point_cloud2"

REAL_CLOUD_FULL_PATH = "/World/RealLidarCloudFull"
REAL_CLOUD_FRONT_PATH = "/World/RealLidarCloudFront"

REAL_CLOUD_MAX_POINTS = 12000
REAL_CLOUD_READ_STRIDE = 8

REAL_CLOUD_POINT_WIDTH_FULL = 0.018
REAL_CLOUD_POINT_WIDTH_FRONT = 0.03

REAL_CLOUD_MIN_RANGE_XY = 0.15
REAL_CLOUD_MAX_RANGE_XY = 8.0

REAL_CLOUD_MIN_Z = -0.10
REAL_CLOUD_MAX_Z = 2.0

REAL_CLOUD_MANUAL_OFFSET_X = 0.0
REAL_CLOUD_MANUAL_OFFSET_Y = 0.0
REAL_CLOUD_MANUAL_OFFSET_Z = 0.0

FRONT_CONE_HALF_ANGLE_DEG = 45.0
FRONT_CONE_MAX_DIST = 3.0
FRONT_CONE_MIN_DIST = 0.15


# ============================================================
# CLEAN PREVIOUS INSTANCE
# ============================================================

if hasattr(builtins, "_go2_dt_update_sub") and builtins._go2_dt_update_sub is not None:
    try:
        builtins._go2_dt_update_sub.unsubscribe()
    except Exception:
        pass
    builtins._go2_dt_update_sub = None

if hasattr(builtins, "_go2_dt_stop_flag"):
    builtins._go2_dt_stop_flag = True

builtins._go2_dt_stop_flag = False


# ============================================================
# ROS SUBSCRIBER
# ============================================================

class Go2DTSubscriber(Node):
    def __init__(self):
        super().__init__("go2_isaac_dt_sync")

        self.create_subscription(PoseStamped, "/go2_dt/base_pose", self.pose_cb, 10)
        self.create_subscription(Float32MultiArray, "/go2_dt/feet_body", self.feet_cb, 10)

        if ENABLE_REAL_LIDAR_CLOUD:
            cloud_qos = QoSProfile(
                reliability=ReliabilityPolicy.BEST_EFFORT,
                history=HistoryPolicy.KEEP_LAST,
                depth=5,
            )
            self.create_subscription(
                PointCloud2,
                REAL_CLOUD_TOPIC,
                self.real_cloud_cb,
                cloud_qos,
            )

        self.get_logger().info(
            f"Subscribed to /go2_dt/base_pose, /go2_dt/feet_body and {REAL_CLOUD_TOPIC}"
        )

    def pose_cb(self, msg):
        global latest_pose, current_raw_world_pos, current_raw_world_quat
        latest_pose = msg

        p = msg.pose.position
        q = msg.pose.orientation

        current_raw_world_pos = (float(p.x), float(p.y), float(p.z))
        current_raw_world_quat = (float(q.x), float(q.y), float(q.z), float(q.w))

    def feet_cb(self, msg):
        global latest_feet
        latest_feet = list(msg.data)

    def real_cloud_cb(self, msg):
        global latest_real_cloud, latest_real_cloud_frame, real_cloud_msg_count

        pts = []
        idx = 0

        try:
            for p in point_cloud2.read_points(
                msg,
                field_names=("x", "y", "z"),
                skip_nans=True,
            ):
                idx += 1
                if idx % REAL_CLOUD_READ_STRIDE != 0:
                    continue

                pts.append((float(p[0]), float(p[1]), float(p[2])))

                if len(pts) >= REAL_CLOUD_MAX_POINTS:
                    break
        except Exception as e:
            print("Error reading PointCloud2:", e)
            return

        latest_real_cloud = pts
        latest_real_cloud_frame = msg.header.frame_id
        real_cloud_msg_count += 1

        if ENABLE_REAL_CLOUD_LOGS and real_cloud_msg_count % 10 == 0:
            print(f"REAL CLOUD msgs={real_cloud_msg_count} points={len(pts)} frame={latest_real_cloud_frame}")


def ros_spin():
    global ros_node
    try:
        if not rclpy.ok():
            rclpy.init()
        ros_node = Go2DTSubscriber()
        while rclpy.ok() and not builtins._go2_dt_stop_flag and ros_node is not None:
            rclpy.spin_once(ros_node, timeout_sec=0.01)
    except Exception as e:
        print("ROS thread stopped:", e)


# ============================================================
# UTILS
# ============================================================

def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def rad2deg(x: float) -> float:
    return x * 180.0 / math.pi


def lerp(a: float, b: float, alpha: float) -> float:
    return a + alpha * (b - a)


def quat_lerp(q1: Gf.Quatd, q2: Gf.Quatd, alpha: float) -> Gf.Quatd:
    w = lerp(q1.GetReal(), q2.GetReal(), alpha)
    i = lerp(q1.GetImaginary()[0], q2.GetImaginary()[0], alpha)
    j = lerp(q1.GetImaginary()[1], q2.GetImaginary()[1], alpha)
    k = lerp(q1.GetImaginary()[2], q2.GetImaginary()[2], alpha)
    return Gf.Quatd(w, Gf.Vec3d(i, j, k)).GetNormalized()


def unpack_feet(data):
    return {
        "FL": tuple(data[0:3]),
        "FR": tuple(data[3:6]),
        "RL": tuple(data[6:9]),
        "RR": tuple(data[9:12]),
    }


def get_or_create_xform_ops(xformable: UsdGeom.Xformable):
    translate_op = None
    orient_op = None

    for op in xformable.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            translate_op = op
        elif op.GetOpType() == UsdGeom.XformOp.TypeOrient:
            orient_op = op

    if translate_op is None:
        translate_op = xformable.AddTranslateOp()
    if orient_op is None:
        orient_op = xformable.AddOrientOp()

    return translate_op, orient_op


def yaw_from_quat_xyzw(x, y, z, w):
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def wrap_angle_deg(a):
    while a > 180.0:
        a -= 360.0
    while a < -180.0:
        a += 360.0
    return a


def rotate_xy(x, y, yaw_rad):
    c = math.cos(yaw_rad)
    s = math.sin(yaw_rad)
    return c * x - s * y, s * x + c * y


def world_to_robot_local(px, py, robot_x, robot_y, robot_yaw):
    dx = px - robot_x
    dy = py - robot_y

    c = math.cos(-robot_yaw)
    s = math.sin(-robot_yaw)

    lx = c * dx - s * dy
    ly = s * dx + c * dy
    return lx, ly


def height_to_rgb(z, zmin=-0.1, zmax=1.5):
    t = 0.0 if zmax <= zmin else (z - zmin) / (zmax - zmin)
    t = max(0.0, min(1.0, t))

    if t < 0.25:
        u = t / 0.25
        return (0.0, u, 1.0)
    elif t < 0.5:
        u = (t - 0.25) / 0.25
        return (0.0, 1.0, 1.0 - u)
    elif t < 0.75:
        u = (t - 0.5) / 0.25
        return (u, 1.0, 0.0)
    else:
        u = (t - 0.75) / 0.25
        return (1.0, 1.0 - u, u)


def quat_from_yaw_deg(yaw_deg):
    yaw_rad = math.radians(yaw_deg)
    cy = math.cos(yaw_rad * 0.5)
    sy = math.sin(yaw_rad * 0.5)
    return Gf.Quatd(cy, Gf.Vec3d(0.0, 0.0, sy))


def get_raw_pose_from_latest_pose():
    if latest_pose is None:
        return None

    p = latest_pose.pose.position
    q = latest_pose.pose.orientation

    real_x = float(p.x)
    real_y = float(p.y)
    real_z = float(p.z)

    qx = float(q.x)
    qy = float(q.y)
    qz = float(q.z)
    qw = float(q.w)

    real_yaw_deg = math.degrees(yaw_from_quat_xyzw(qx, qy, qz, qw))

    return {
        "x": real_x,
        "y": real_y,
        "z": real_z,
        "qx": qx,
        "qy": qy,
        "qz": qz,
        "qw": qw,
        "yaw_deg": real_yaw_deg,
    }


def print_current_real_pose():
    raw = get_raw_pose_from_latest_pose()

    if raw is None:
        print("No real pose yet")
        return

    print(f"REAL POSITION: ({raw['x']}, {raw['y']}, {raw['z']})")
    print(f"REAL QUAT: ({raw['qx']}, {raw['qy']}, {raw['qz']}, {raw['qw']})")
    print(f"REAL YAW_DEG: {raw['yaw_deg']}")


def print_current_anchor():
    print("SESSION ANCHOR")
    print(f"  ready={session_anchor_ready}")
    print(f"  real_anchor=({session_real_anchor_x}, {session_real_anchor_y}, {session_real_anchor_z})")
    print(f"  real_anchor_yaw_deg={session_real_anchor_yaw_deg}")
    print(f"  yaw_offset_deg={session_yaw_offset_deg}")
    print(f"  fixed_isaac=({FIXED_START_ISAAC_X}, {FIXED_START_ISAAC_Y}, {FIXED_START_ISAAC_YAW_DEG})")
    print(f"  last_patrol_event_id={patrol_last_processed_event_id}")


# ============================================================
# VISIBILITY HELPERS
# ============================================================

def set_prim_visibility(stage, prim_path, visible):
    prim = stage.GetPrimAtPath(prim_path)
    if not prim.IsValid():
        print(f"[VISIBILITY] Prim not found: {prim_path}")
        return False

    imageable = UsdGeom.Imageable(prim)
    if not imageable:
        print(f"[VISIBILITY] Prim is not imageable: {prim_path}")
        return False

    if visible:
        imageable.MakeVisible()
    else:
        imageable.MakeInvisible()

    return True


# ============================================================
# PATROL / APRILTAG ANCHOR RESET HELPERS
# ============================================================

def resolve_patrol_json_path():
    candidate_paths = []

    if PATROL_STATE_JSON_PATH:
        candidate_paths.append(PATROL_STATE_JSON_PATH)

    for p in PATROL_STATE_JSON_FALLBACK_PATHS:
        if p not in candidate_paths:
            candidate_paths.append(p)

    for path in candidate_paths:
        if os.path.exists(path):
            return path

    return PATROL_STATE_JSON_PATH


def read_patrol_state_json(json_path):
    if not os.path.exists(json_path):
        return None

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[PATROL] Error reading JSON: {e}")
        return None


def extract_start_pose_event(state):
    if not isinstance(state, dict):
        return None

    event = state.get("start_pose_event", {})
    if not isinstance(event, dict):
        return None

    if not bool(event.get("active", False)):
        return None

    try:
        event_id = int(event.get("event_id", 0))
    except Exception:
        return None

    if event_id <= 0:
        return None

    return {
        "event_id": event_id,
        "lap_index": event.get("lap_index"),
        "timestamp": event.get("timestamp"),
    }


def force_robot_to_fixed_start_pose(stage):
    """
    Fuerza visualmente el robot al inicio fijo de Isaac.
    Se aplica sobre ARTICULATION_ROOT_PATH porque es el mismo prim que mueve apply_pose().
    """
    global current_base_pos, current_base_quat, current_sim_yaw_rad

    prim = stage.GetPrimAtPath(ARTICULATION_ROOT_PATH)
    if not prim.IsValid():
        print(f"[PATROL][ANCHOR_RESET] Robot articulation prim not found: {ARTICULATION_ROOT_PATH}")
        return False

    xformable = UsdGeom.Xformable(prim)
    translate_op, orient_op = get_or_create_xform_ops(xformable)

    current_base_pos = Gf.Vec3d(FIXED_START_ISAAC_X, FIXED_START_ISAAC_Y, BASE_Z_VALUE)
    current_base_quat = quat_from_yaw_deg(FIXED_START_ISAAC_YAW_DEG)
    current_sim_yaw_rad = math.radians(FIXED_START_ISAAC_YAW_DEG)

    translate_op.Set(current_base_pos)
    orient_op.Set(current_base_quat)

    return True


def reset_session_anchor_from_latest_pose(stage, reason="manual", event_id=None, lap_index=None):
    """
    Recalibra la transformación real->Isaac usando la pose ROS actual como nuevo cero.

    Caso típico:
    - El Go2 vuelve físicamente al inicio.
    - AprilTag confirma que está bien recolocado.
    - zone_loop_patrol_v3.py incrementa start_pose_event.event_id.
    - Isaac recibe el evento y hace:

        real_pose_actual  ->  FIXED_START_ISAAC_X/Y/YAW

    A partir de ahí:
    - Si ROS dice yaw=90 en ese instante, Isaac lo trata como FIXED_START_ISAAC_YAW_DEG.
    - Si luego ROS pasa a yaw=100, Isaac interpreta un giro relativo de +10 grados.
    """
    global session_anchor_ready
    global session_real_anchor_x, session_real_anchor_y, session_real_anchor_z
    global session_real_anchor_yaw_deg, session_yaw_offset_deg
    global warmup_counter
    global anchor_reset_freeze_frames_left
    global patrol_last_reset_monotonic
    global current_raw_world_pos, current_raw_world_quat

    if not USE_FIXED_START_ANCHOR:
        print("[PATROL][ANCHOR_RESET] Ignored because USE_FIXED_START_ANCHOR=False")
        return False

    raw = get_raw_pose_from_latest_pose()
    if raw is None:
        print("[PATROL][ANCHOR_RESET] Cannot reset anchor: no latest_pose yet")
        return False

    session_real_anchor_x = raw["x"]
    session_real_anchor_y = raw["y"]
    session_real_anchor_z = raw["z"]
    session_real_anchor_yaw_deg = raw["yaw_deg"]

    current_raw_world_pos = (raw["x"], raw["y"], raw["z"])
    current_raw_world_quat = (raw["qx"], raw["qy"], raw["qz"], raw["qw"])

    # CLAVE:
    # Queremos que en este instante:
    #   sim_yaw = FIXED_START_ISAAC_YAW_DEG
    #
    # Como:
    #   sim_yaw = real_yaw + session_yaw_offset_deg
    #
    # Entonces:
    #   session_yaw_offset_deg = FIXED_START_ISAAC_YAW_DEG - real_yaw
    session_yaw_offset_deg = wrap_angle_deg(FIXED_START_ISAAC_YAW_DEG - session_real_anchor_yaw_deg)
    session_anchor_ready = True

    ok = force_robot_to_fixed_start_pose(stage)

    warmup_counter = int(ANCHOR_RESET_WARMUP_FRAMES)
    anchor_reset_freeze_frames_left = int(ANCHOR_RESET_FREEZE_FRAMES)
    patrol_last_reset_monotonic = time.monotonic()

    print("[PATROL][ANCHOR_RESET] SESSION ANCHOR RESET")
    print(f"  reason={reason} event_id={event_id} lap_index={lap_index}")
    print(f"  real anchor x={session_real_anchor_x:.6f} y={session_real_anchor_y:.6f} z={session_real_anchor_z:.6f}")
    print(f"  real anchor yaw={session_real_anchor_yaw_deg:.6f}")
    print(f"  fixed Isaac x={FIXED_START_ISAAC_X:.6f} y={FIXED_START_ISAAC_Y:.6f} yaw={FIXED_START_ISAAC_YAW_DEG:.6f}")
    print(f"  new yaw offset={session_yaw_offset_deg:.6f}")
    print(f"  forced_fixed_pose={ok}")
    print(f"  warmup_frames={warmup_counter} freeze_frames={anchor_reset_freeze_frames_left}")

    return ok


def update_patrol_anchor_reset(stage):
    """
    Devuelve True si se acaba de procesar un nuevo start_pose_event.
    """
    global patrol_last_poll_monotonic
    global patrol_last_file_mtime
    global patrol_last_state
    global patrol_last_processed_event_id

    if not ENABLE_PATROL_ANCHOR_RESET:
        return False

    now_mono = time.monotonic()
    if now_mono - patrol_last_poll_monotonic < PATROL_POLL_INTERVAL_SEC:
        return False

    patrol_last_poll_monotonic = now_mono

    json_path = resolve_patrol_json_path()

    if not os.path.exists(json_path):
        if PATROL_ANCHOR_DEBUG_LOGS and patrol_last_state is None:
            print(f"[PATROL] JSON not found yet: {json_path}")
        return False

    try:
        mtime = os.path.getmtime(json_path)
    except Exception as e:
        print(f"[PATROL] mtime error: {e}")
        return False

    age_sec = time.time() - mtime
    if age_sec > PATROL_STATE_MAX_AGE_SEC:
        if PATROL_ANCHOR_DEBUG_LOGS:
            print(f"[PATROL] JSON stale: age={age_sec:.2f}s path={json_path}")
        return False

    if patrol_last_file_mtime != mtime:
        patrol_last_file_mtime = mtime
        patrol_last_state = read_patrol_state_json(json_path)

        if PATROL_ANCHOR_DEBUG_LOGS and isinstance(patrol_last_state, dict):
            event = patrol_last_state.get("start_pose_event", {})
            print(
                "[PATROL] read JSON | "
                f"path={json_path} | "
                f"state={patrol_last_state.get('state')} | "
                f"lap={patrol_last_state.get('lap_index')} | "
                f"event_id={event.get('event_id') if isinstance(event, dict) else None} | "
                f"event_active={event.get('active') if isinstance(event, dict) else None}"
            )

    event = extract_start_pose_event(patrol_last_state)
    if event is None:
        return False

    event_id = int(event["event_id"])
    if event_id <= patrol_last_processed_event_id:
        return False

    ok = reset_session_anchor_from_latest_pose(
        stage,
        reason="patrol_start_pose_event",
        event_id=event_id,
        lap_index=event.get("lap_index"),
    )

    if ok:
        patrol_last_processed_event_id = event_id
        print(f"[PATROL] processed start_pose_event id={event_id}")
        return True

    return False


def manual_force_anchor_reset():
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        print("[MANUAL_ANCHOR_RESET] No stage")
        return False

    return reset_session_anchor_from_latest_pose(
        stage,
        reason="manual_debug_command",
        event_id=None,
        lap_index=None,
    )


# ============================================================
# mmWave / CSI HELPERS
# ============================================================

def resolve_mmwave_json_path():
    candidate_paths = []

    if MMWAVE_STATE_JSON_PATH:
        candidate_paths.append(MMWAVE_STATE_JSON_PATH)

    for p in MMWAVE_STATE_JSON_FALLBACK_PATHS:
        if p not in candidate_paths:
            candidate_paths.append(p)

    for path in candidate_paths:
        if os.path.exists(path):
            return path

    return MMWAVE_STATE_JSON_PATH


def read_mmwave_state_json(json_path):
    if not os.path.exists(json_path):
        return None

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[MMWAVE] Error reading JSON: {e}")
        return None


def normalize_mmwave_prediction(state):
    if not isinstance(state, dict):
        return "unknown"

    if "person_detected" in state:
        return "person" if bool(state.get("person_detected")) else "empty"

    if "occupied" in state:
        return "person" if bool(state.get("occupied")) else "empty"

    prediction = str(state.get("prediction", "")).strip().lower()
    raw_prediction = str(state.get("raw_prediction", "")).strip().lower()
    label = str(state.get("label", "")).strip().lower()

    candidates = [prediction, raw_prediction, label]

    for value in candidates:
        if value in MMWAVE_PERSON_LABELS:
            return "person"

        if value in MMWAVE_EMPTY_LABELS:
            return "empty"

    try:
        count = int(state.get("count", 0))
        if count > 0:
            return "person"
    except Exception:
        pass

    return prediction or raw_prediction or label or "unknown"


def apply_mmwave_person_visibility(stage, visible, reason):
    global mmwave_last_visibility

    if mmwave_last_visibility.get(MMWAVE_PERSON_PRIM_PATH) == visible:
        return

    ok = set_prim_visibility(stage, MMWAVE_PERSON_PRIM_PATH, visible)
    if ok:
        mmwave_last_visibility[MMWAVE_PERSON_PRIM_PATH] = visible
        print(f"[MMWAVE] {MMWAVE_PERSON_PRIM_PATH} visible={visible} reason={reason}")


def update_mmwave_person_visibility(stage):
    global mmwave_last_poll_monotonic
    global mmwave_last_file_mtime
    global mmwave_last_state

    if not ENABLE_MMWAVE_PERSON_VISIBILITY:
        return

    now_mono = time.monotonic()
    if now_mono - mmwave_last_poll_monotonic < MMWAVE_POLL_INTERVAL_SEC:
        return

    mmwave_last_poll_monotonic = now_mono

    json_path = resolve_mmwave_json_path()

    if not os.path.exists(json_path):
        apply_mmwave_person_visibility(stage, False, "missing_json")
        print(f"[MMWAVE] JSON not found: {json_path}")
        return

    try:
        mtime = os.path.getmtime(json_path)
    except Exception as e:
        print(f"[MMWAVE] mtime error: {e}")
        if MMWAVE_HIDE_ON_ERROR:
            apply_mmwave_person_visibility(stage, False, "mtime_error")
        return

    if mmwave_last_file_mtime != mtime:
        mmwave_last_file_mtime = mtime
        mmwave_last_state = read_mmwave_state_json(json_path)

        if isinstance(mmwave_last_state, dict):
            print(
                "[MMWAVE] read JSON | "
                f"path={json_path} | "
                f"prediction={mmwave_last_state.get('prediction')} | "
                f"raw_prediction={mmwave_last_state.get('raw_prediction')} | "
                f"score={mmwave_last_state.get('score')} | "
                f"streak={mmwave_last_state.get('streak')} | "
                f"stable_votes={mmwave_last_state.get('stable_votes')}"
            )

    if mmwave_last_state is None:
        if MMWAVE_HIDE_ON_ERROR:
            apply_mmwave_person_visibility(stage, False, "read_error")
        return

    age_sec = time.time() - mtime
    if MMWAVE_HIDE_WHEN_STALE and age_sec > MMWAVE_STATE_MAX_AGE_SEC:
        apply_mmwave_person_visibility(stage, False, f"stale_{age_sec:.2f}s")
        return

    normalized = normalize_mmwave_prediction(mmwave_last_state)

    if normalized == "person":
        apply_mmwave_person_visibility(stage, True, "prediction_person")
    elif normalized == "empty":
        apply_mmwave_person_visibility(stage, False, "prediction_empty")
    else:
        if MMWAVE_HIDE_ON_ERROR:
            apply_mmwave_person_visibility(stage, False, f"unknown_{normalized}")


def initialize_mmwave_person_visibility(stage):
    if not ENABLE_MMWAVE_PERSON_VISIBILITY:
        return

    apply_mmwave_person_visibility(stage, False, "init")
    print(f"[MMWAVE] Initial visibility set to False for {MMWAVE_PERSON_PRIM_PATH}")


# ============================================================
# GENERIC LIDAR / CAMERA JSON HELPERS
# ============================================================

def infer_person_detected(state):
    if not isinstance(state, dict):
        return False

    if "person_detected" in state:
        return bool(state.get("person_detected"))

    if "occupied" in state:
        return bool(state.get("occupied"))

    count = state.get("count", 0)
    try:
        if int(count) > 0:
            return True
    except Exception:
        pass

    label = str(
        state.get("prediction", state.get("raw_prediction", state.get("label", "")))
    ).strip().lower()

    return label in {
        "person",
        "person_1",
        "person_2",
        "person_3",
        "occupied",
        "true",
    }


def sensor_state_online(state):
    if not isinstance(state, dict):
        return False

    status = str(state.get("status", "online")).strip().lower()
    return status not in {"fault", "offline", "stale"}


def read_generic_state_json(json_path, tag):
    if not os.path.exists(json_path):
        return None

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[{tag}] Error reading JSON: {e}")
        return None


def apply_person_visibility(stage, prim_path, visible, tag, reason):
    global sensor_last_visibility

    if sensor_last_visibility.get(prim_path) == visible:
        return

    ok = set_prim_visibility(stage, prim_path, visible)
    if ok:
        sensor_last_visibility[prim_path] = visible
        print(f"[{tag}] {prim_path} visible={visible} reason={reason}")


def update_single_person_visibility(stage, tag, json_path, prim_path):
    global sensor_last_file_mtime, sensor_last_state

    if not os.path.exists(json_path):
        apply_person_visibility(stage, prim_path, False, tag, "missing_json")
        return

    try:
        mtime = os.path.getmtime(json_path)
    except Exception:
        if SENSOR_HIDE_ON_ERROR:
            apply_person_visibility(stage, prim_path, False, tag, "mtime_error")
        return

    if sensor_last_file_mtime.get(json_path) != mtime:
        sensor_last_file_mtime[json_path] = mtime
        sensor_last_state[json_path] = read_generic_state_json(json_path, tag)

    state = sensor_last_state.get(json_path)
    if state is None:
        if SENSOR_HIDE_ON_ERROR:
            apply_person_visibility(stage, prim_path, False, tag, "error")
        return

    age_sec = time.time() - mtime
    if SENSOR_HIDE_WHEN_STALE and age_sec > SENSOR_STATE_MAX_AGE_SEC:
        apply_person_visibility(stage, prim_path, False, tag, "stale")
        return

    if not sensor_state_online(state):
        apply_person_visibility(stage, prim_path, False, tag, "offline")
        return

    visible = infer_person_detected(state)
    apply_person_visibility(stage, prim_path, visible, tag, "person_detected" if visible else "clear")


def update_sensor_person_visibility(stage):
    global sensor_last_poll_monotonic

    if not ENABLE_SENSOR_PERSON_VISIBILITY:
        return

    now_mono = time.monotonic()
    if now_mono - sensor_last_poll_monotonic < MMWAVE_POLL_INTERVAL_SEC:
        return

    sensor_last_poll_monotonic = now_mono

    update_single_person_visibility(stage, "LIDAR", LIDAR_STATE_JSON_PATH, LIDAR_PERSON_PRIM_PATH)
    update_single_person_visibility(stage, "CAMERA", CAMERA_STATE_JSON_PATH, CAMERA_PERSON_PRIM_PATH)


def initialize_sensor_person_visibility(stage):
    if not ENABLE_SENSOR_PERSON_VISIBILITY:
        return

    apply_person_visibility(stage, LIDAR_PERSON_PRIM_PATH, False, "LIDAR", "init")
    apply_person_visibility(stage, CAMERA_PERSON_PRIM_PATH, False, "CAMERA", "init")


# ============================================================
# SESSION ANCHOR
# ============================================================

def ensure_session_anchor():
    global session_anchor_ready
    global session_real_anchor_x, session_real_anchor_y, session_real_anchor_z
    global session_real_anchor_yaw_deg, session_yaw_offset_deg
    global current_raw_world_pos, current_raw_world_quat

    if session_anchor_ready:
        return True

    if not USE_FIXED_START_ANCHOR:
        return False

    raw = get_raw_pose_from_latest_pose()
    if raw is None:
        return False

    session_real_anchor_x = raw["x"]
    session_real_anchor_y = raw["y"]
    session_real_anchor_z = raw["z"]
    session_real_anchor_yaw_deg = raw["yaw_deg"]

    current_raw_world_pos = (raw["x"], raw["y"], raw["z"])
    current_raw_world_quat = (raw["qx"], raw["qy"], raw["qz"], raw["qw"])

    session_yaw_offset_deg = wrap_angle_deg(FIXED_START_ISAAC_YAW_DEG - session_real_anchor_yaw_deg)

    session_anchor_ready = True

    print("SESSION ANCHOR READY")
    print(f"  real start x={session_real_anchor_x:.6f} y={session_real_anchor_y:.6f} z={session_real_anchor_z:.6f}")
    print(f"  real start yaw={session_real_anchor_yaw_deg:.6f}")
    print(f"  isaac fixed x={FIXED_START_ISAAC_X:.6f} y={FIXED_START_ISAAC_Y:.6f} yaw={FIXED_START_ISAAC_YAW_DEG:.6f}")
    print(f"  yaw offset={session_yaw_offset_deg:.6f}")

    return True


def transform_real_pose_to_isaac(real_x, real_y, real_z, real_yaw_deg):
    if not ensure_session_anchor():
        return None

    dx = real_x - session_real_anchor_x
    dy = real_y - session_real_anchor_y
    dz = real_z - session_real_anchor_z

    yaw_off_rad = math.radians(session_yaw_offset_deg)
    rdx, rdy = rotate_xy(dx, dy, yaw_off_rad)

    rdx *= REAL_TO_ISAAC_SCALE_X
    rdy *= REAL_TO_ISAAC_SCALE_Y

    sim_x = FIXED_START_ISAAC_X + rdx
    sim_y = FIXED_START_ISAAC_Y + rdy
    sim_z = BASE_Z_VALUE if FORCE_BASE_Z_TO_ZERO else (BASE_Z_VALUE + dz + BASE_Z_OFFSET)

    sim_yaw_deg = wrap_angle_deg(real_yaw_deg + session_yaw_offset_deg)

    return sim_x, sim_y, sim_z, sim_yaw_deg


def transform_real_cloud_to_isaac_world(points_xyz):
    if not ensure_session_anchor():
        return []

    transformed = []

    yaw_off_rad = math.radians(session_yaw_offset_deg)

    max_r2 = REAL_CLOUD_MAX_RANGE_XY * REAL_CLOUD_MAX_RANGE_XY
    min_r2 = REAL_CLOUD_MIN_RANGE_XY * REAL_CLOUD_MIN_RANGE_XY

    for x, y, z in points_xyz:
        dx = x - session_real_anchor_x
        dy = y - session_real_anchor_y
        dz = z - session_real_anchor_z

        r2 = dx * dx + dy * dy
        if r2 < min_r2 or r2 > max_r2:
            continue
        if dz < REAL_CLOUD_MIN_Z or dz > REAL_CLOUD_MAX_Z:
            continue

        rdx, rdy = rotate_xy(dx, dy, yaw_off_rad)

        rdx *= REAL_TO_ISAAC_SCALE_X
        rdy *= REAL_TO_ISAAC_SCALE_Y

        px = FIXED_START_ISAAC_X + rdx + REAL_CLOUD_MANUAL_OFFSET_X
        py = FIXED_START_ISAAC_Y + rdy + REAL_CLOUD_MANUAL_OFFSET_Y
        pz = BASE_Z_VALUE + dz + REAL_CLOUD_MANUAL_OFFSET_Z

        transformed.append((px, py, pz))

    return transformed


# ============================================================
# SCENE SETUP
# ============================================================

def setup_articulation_root(stage):
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            prim.RemoveAPI(UsdPhysics.ArticulationRootAPI)

    root_prim = stage.GetPrimAtPath(ARTICULATION_ROOT_PATH)
    if not root_prim.IsValid():
        raise RuntimeError(f"No existe {ARTICULATION_ROOT_PATH}")

    UsdPhysics.ArticulationRootAPI.Apply(root_prim)

    print("Final articulation roots:")
    for prim in stage.Traverse():
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            print(" -", prim.GetPath().pathString)


def disable_robot_collisions(stage):
    count = 0
    for prim in stage.Traverse():
        path = prim.GetPath().pathString
        if "/collisions" in path and prim.HasAPI(UsdPhysics.CollisionAPI):
            prim.RemoveAPI(UsdPhysics.CollisionAPI)
            count += 1
    print(f"Robot collisions disabled on {count} prims")


def disable_robot_gravity(stage):
    count = 0
    for prim in stage.Traverse():
        path = prim.GetPath().pathString
        if not path.startswith(ROBOT_XFORM_PATH):
            continue

        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            physx_rb = PhysxSchema.PhysxRigidBodyAPI.Apply(prim)
            physx_rb.CreateDisableGravityAttr(True)
            count += 1
            print("Gravity disabled on:", path)

    print(f"Rigid bodies updated: {count}")


def setup_joint_drives(stage):
    joint_names = [
        "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
        "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
        "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint",
        "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
    ]

    for name in joint_names:
        joint_prim = stage.GetPrimAtPath(f"{JOINT_SCOPE}/{name}")
        if not joint_prim.IsValid():
            print("Missing joint:", name)
            continue

        drive = UsdPhysics.DriveAPI.Apply(joint_prim, "angular")
        drive.CreateStiffnessAttr(DRIVE_STIFFNESS)
        drive.CreateDampingAttr(DRIVE_DAMPING)
        drive.CreateMaxForceAttr(DRIVE_MAX_FORCE)
        drive.CreateTargetPositionAttr(0.0)

        print("Drive ready:", name)

    print("Joint drives configured")


def ensure_lidar_parent_exists(stage):
    parent = stage.GetPrimAtPath(LIDAR_PARENT)
    if not parent.IsValid():
        raise RuntimeError(f"No existe el frame radar: {LIDAR_PARENT}")
    return parent


def ensure_visual_lidar_body(stage):
    ensure_lidar_parent_exists(stage)

    prim = stage.GetPrimAtPath(LIDAR_BODY_PATH)
    if prim.IsValid():
        print("Visual lidar body already exists:", LIDAR_BODY_PATH)
        return

    cyl = UsdGeom.Cylinder.Define(stage, LIDAR_BODY_PATH)
    cyl.CreateRadiusAttr(LIDAR_BODY_RADIUS)
    cyl.CreateHeightAttr(LIDAR_BODY_HEIGHT)
    cyl.CreateAxisAttr("Z")
    cyl.CreateDisplayColorAttr([(0.12, 0.12, 0.12)])

    xform = UsdGeom.XformCommonAPI(cyl.GetPrim())
    xform.SetTranslate((0.0, 0.0, LIDAR_BODY_Z))

    print("Visual lidar body created:", LIDAR_BODY_PATH)


def ensure_simulated_head_lidar(stage):
    ensure_lidar_parent_exists(stage)

    prim = stage.GetPrimAtPath(LIDAR_SENSOR_PATH)
    if prim.IsValid():
        print("Simulated lidar already exists:", LIDAR_SENSOR_PATH)
        return

    _, lidar_prim = omni.kit.commands.execute(
        "RangeSensorCreateLidar",
        path=LIDAR_PRIM_NAME,
        parent=LIDAR_PARENT,
        min_range=LIDAR_MIN_RANGE,
        max_range=LIDAR_MAX_RANGE,
        draw_points=DRAW_LIDAR_POINTS,
        draw_lines=DRAW_LIDAR_LINES,
        horizontal_fov=LIDAR_HORIZONTAL_FOV,
        vertical_fov=LIDAR_VERTICAL_FOV,
        horizontal_resolution=LIDAR_HORIZONTAL_RESOLUTION,
        vertical_resolution=LIDAR_VERTICAL_RESOLUTION,
        rotation_rate=LIDAR_ROTATION_RATE,
        high_lod=True,
        yaw_offset=0.0,
        enable_semantics=False,
    )

    UsdGeom.XformCommonAPI(lidar_prim).SetTranslate((0.0, 0.0, LIDAR_SENSOR_Z))
    print("Simulated head lidar created:", LIDAR_SENSOR_PATH)


def create_points_prim(stage, path):
    prim = stage.GetPrimAtPath(path)
    if prim.IsValid():
        stage.RemovePrim(path)

    points_prim = UsdGeom.Points.Define(stage, path)
    points_prim.CreatePointsAttr([])
    points_prim.CreateWidthsAttr([])
    points_prim.CreateDisplayColorAttr([])


def ensure_real_cloud_prims(stage):
    create_points_prim(stage, REAL_CLOUD_FULL_PATH)
    create_points_prim(stage, REAL_CLOUD_FRONT_PATH)
    print("Real lidar cloud prims created")


def set_points_geom(path, points, widths, colors):
    stage = omni.usd.get_context().get_stage()
    prim = stage.GetPrimAtPath(path)
    if not prim.IsValid():
        return

    geom = UsdGeom.Points(prim)
    if not geom:
        return

    geom.GetPointsAttr().Set(Vt.Vec3fArray([Gf.Vec3f(*p) for p in points]))
    geom.GetWidthsAttr().Set(widths)
    geom.GetDisplayColorAttr().Set(colors)


def apply_real_cloud(stage, raw_points_xyz):
    global real_cloud_apply_count

    if raw_points_xyz is None:
        return

    world_points = transform_real_cloud_to_isaac_world(raw_points_xyz)
    if not world_points:
        set_points_geom(REAL_CLOUD_FULL_PATH, [], [], [])
        set_points_geom(REAL_CLOUD_FRONT_PATH, [], [], [])
        return

    full_points = world_points

    front_points = []
    half_angle = math.radians(FRONT_CONE_HALF_ANGLE_DEG)

    if current_base_pos is not None and current_sim_yaw_rad is not None:
        robot_x = float(current_base_pos[0])
        robot_y = float(current_base_pos[1])

        for px, py, pz in full_points:
            lx, ly = world_to_robot_local(px, py, robot_x, robot_y, current_sim_yaw_rad)
            dist = math.sqrt(lx * lx + ly * ly)

            if dist < FRONT_CONE_MIN_DIST or dist > FRONT_CONE_MAX_DIST:
                continue

            ang = math.atan2(ly, lx)
            if abs(ang) <= half_angle and lx > 0.0:
                front_points.append((px, py, pz))

    full_colors = [height_to_rgb(p[2]) for p in full_points]
    front_colors = [(1.0, 0.15, 0.15) for _ in front_points]

    full_widths = [REAL_CLOUD_POINT_WIDTH_FULL] * len(full_points)
    front_widths = [REAL_CLOUD_POINT_WIDTH_FRONT] * len(front_points)

    set_points_geom(REAL_CLOUD_FULL_PATH, full_points, full_widths, full_colors)
    set_points_geom(REAL_CLOUD_FRONT_PATH, front_points, front_widths, front_colors)

    real_cloud_apply_count += 1
    if ENABLE_REAL_CLOUD_LOGS and real_cloud_apply_count % 20 == 0:
        print(
            f"REAL CLOUD full={len(full_points)} front={len(front_points)} mode=session_rigid_transform"
        )


# ============================================================
# IK
# ============================================================

def solve_leg_ik(leg_name, foot_body):
    hx, hy, hz = HIP_ORIGINS[leg_name]
    lateral = THIGH_LATERAL[leg_name]

    x = foot_body[0] - hx
    y = foot_body[1] - hy
    z = foot_body[2] - hz

    y = y - lateral
    hip = math.atan2(y, -z)

    z_eff = -math.sqrt(max(1e-9, y * y + z * z))
    L = math.sqrt(x * x + z_eff * z_eff)
    L = clamp(L, 1e-6, UPPER_LEG + LOWER_LEG - 1e-6)

    cos_knee = clamp(
        (UPPER_LEG * UPPER_LEG + LOWER_LEG * LOWER_LEG - L * L)
        / (2.0 * UPPER_LEG * LOWER_LEG),
        -1.0,
        1.0,
    )
    knee_inner = math.acos(cos_knee)
    calf = -knee_inner if NEGATIVE_KNEE else knee_inner

    cos_thigh = clamp(
        (UPPER_LEG * UPPER_LEG + L * L - LOWER_LEG * LOWER_LEG)
        / (2.0 * UPPER_LEG * L),
        -1.0,
        1.0,
    )
    alpha = math.acos(cos_thigh)
    beta = math.atan2(x, -z_eff)
    thigh = alpha - beta

    return hip, thigh, calf


def set_joint_target_deg(stage, joint_path, degrees_value):
    prim = stage.GetPrimAtPath(joint_path)
    if not prim.IsValid():
        return

    drive = UsdPhysics.DriveAPI.Get(prim, "angular")
    if not drive:
        drive = UsdPhysics.DriveAPI.Apply(prim, "angular")

    attr = drive.GetTargetPositionAttr()
    if not attr:
        attr = drive.CreateTargetPositionAttr()

    attr.Set(float(degrees_value))


# ============================================================
# APPLY STATE TO ISAAC
# ============================================================

def apply_pose(stage, pose_msg, alpha=None):
    global current_base_pos, current_base_quat, current_sim_yaw_rad
    global current_raw_world_pos, current_raw_world_quat

    prim = stage.GetPrimAtPath(ARTICULATION_ROOT_PATH)
    if not prim.IsValid():
        return

    xformable = UsdGeom.Xformable(prim)
    translate_op, orient_op = get_or_create_xform_ops(xformable)

    p = pose_msg.pose.position
    q = pose_msg.pose.orientation

    real_x = float(p.x)
    real_y = float(p.y)
    real_z = float(p.z)

    qx = float(q.x)
    qy = float(q.y)
    qz = float(q.z)
    qw = float(q.w)

    current_raw_world_pos = (real_x, real_y, real_z)
    current_raw_world_quat = (qx, qy, qz, qw)

    real_yaw_deg = math.degrees(yaw_from_quat_xyzw(qx, qy, qz, qw))

    transformed = transform_real_pose_to_isaac(real_x, real_y, real_z, real_yaw_deg)
    if transformed is None:
        return

    sim_x, sim_y, sim_z, sim_yaw_deg = transformed
    current_sim_yaw_rad = math.radians(sim_yaw_deg)

    target_pos = Gf.Vec3d(sim_x, sim_y, sim_z)
    target_quat = quat_from_yaw_deg(sim_yaw_deg)

    if alpha is None or current_base_pos is None or current_base_quat is None:
        current_base_pos = target_pos
        current_base_quat = target_quat
    else:
        current_base_pos = Gf.Vec3d(
            lerp(current_base_pos[0], target_pos[0], alpha),
            lerp(current_base_pos[1], target_pos[1], alpha),
            lerp(current_base_pos[2], target_pos[2], alpha),
        )
        current_base_quat = quat_lerp(current_base_quat, target_quat, alpha)

    translate_op.Set(current_base_pos)
    orient_op.Set(current_base_quat)


def apply_legs(stage, feet_data):
    feet = unpack_feet(feet_data)

    for leg in ["FL", "FR", "RL", "RR"]:
        hip, thigh, calf = solve_leg_ik(leg, feet[leg])

        set_joint_target_deg(stage, JOINT_PATHS[f"{leg}_hip"], rad2deg(hip))
        set_joint_target_deg(stage, JOINT_PATHS[f"{leg}_thigh"], rad2deg(thigh))
        set_joint_target_deg(stage, JOINT_PATHS[f"{leg}_calf"], rad2deg(calf))


# ============================================================
# UPDATE LOOP
# ============================================================

def on_update(event):
    global latest_pose, latest_feet, latest_real_cloud
    global initialized_once, warmup_counter
    global anchor_reset_freeze_frames_left

    stage = omni.usd.get_context().get_stage()

    update_mmwave_person_visibility(stage)
    update_sensor_person_visibility(stage)

    if latest_pose is None or latest_feet is None:
        return

    just_reset = update_patrol_anchor_reset(stage)

    if not ensure_session_anchor():
        return

    # Si acaba de llegar start_pose_event, Isaac debe quedar clavado en el inicio fijo.
    # Después se deja que apply_pose siga usando el nuevo anchor.
    if just_reset:
        apply_pose(stage, latest_pose, alpha=None)
        apply_legs(stage, latest_feet)

        if ENABLE_REAL_LIDAR_CLOUD and latest_real_cloud is not None:
            apply_real_cloud(stage, latest_real_cloud)

        return

    # Pequeño freeze adicional tras reset para evitar arrastres por frames de smoothing.
    if anchor_reset_freeze_frames_left > 0:
        anchor_reset_freeze_frames_left -= 1
        force_robot_to_fixed_start_pose(stage)
        apply_legs(stage, latest_feet)

        if ENABLE_REAL_LIDAR_CLOUD and latest_real_cloud is not None:
            apply_real_cloud(stage, latest_real_cloud)

        return

    if not initialized_once:
        apply_pose(stage, latest_pose, alpha=None)
        apply_legs(stage, latest_feet)

        if ENABLE_REAL_LIDAR_CLOUD and latest_real_cloud is not None:
            apply_real_cloud(stage, latest_real_cloud)

        initialized_once = True
        print("Initial pose and legs applied")
        return

    if warmup_counter > 0:
        warmup_counter -= 1
        apply_pose(stage, latest_pose, alpha=None)
        apply_legs(stage, latest_feet)

        if ENABLE_REAL_LIDAR_CLOUD and latest_real_cloud is not None:
            apply_real_cloud(stage, latest_real_cloud)

        return

    apply_pose(stage, latest_pose, alpha=POSE_SMOOTHING_ALPHA)
    apply_legs(stage, latest_feet)

    if ENABLE_REAL_LIDAR_CLOUD and latest_real_cloud is not None:
        apply_real_cloud(stage, latest_real_cloud)


# ============================================================
# MAIN
# ============================================================

def main():
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        raise RuntimeError("No hay stage abierto en Isaac")

    robot_prim = stage.GetPrimAtPath(ROBOT_XFORM_PATH)
    if not robot_prim.IsValid():
        raise RuntimeError(f"No existe {ROBOT_XFORM_PATH}")

    print("Preparing Go2 DT scene...")
    print("IMPORTANT: put the REAL robot always in the SAME physical start pose before running this script.")
    print(f"Fixed Isaac start: x={FIXED_START_ISAAC_X}, y={FIXED_START_ISAAC_Y}, yaw_deg={FIXED_START_ISAAC_YAW_DEG}")
    print(f"Patrol anchor reset enabled={ENABLE_PATROL_ANCHOR_RESET}")
    print(f"Patrol JSON primary path={PATROL_STATE_JSON_PATH}")
    print("Yaw coherence mode:")
    print("  sim_yaw = real_yaw + session_yaw_offset_deg")
    print("  on AprilTag reset: session_yaw_offset_deg = FIXED_START_ISAAC_YAW_DEG - current_real_yaw")
    print("  therefore future rotations remain relative to the corrected start.")

    setup_articulation_root(stage)
    disable_robot_collisions(stage)
    disable_robot_gravity(stage)
    setup_joint_drives(stage)

    ensure_visual_lidar_body(stage)

    if ENABLE_SIMULATED_LIDAR:
        ensure_simulated_head_lidar(stage)

    if ENABLE_REAL_LIDAR_CLOUD:
        ensure_real_cloud_prims(stage)

    if ENABLE_MMWAVE_PERSON_VISIBILITY:
        initialize_mmwave_person_visibility(stage)

    if ENABLE_SENSOR_PERSON_VISIBILITY:
        initialize_sensor_person_visibility(stage)

    builtins.go2_print_real_pose = print_current_real_pose
    builtins.go2_print_anchor = print_current_anchor
    builtins.go2_force_anchor_reset = manual_force_anchor_reset

    global ros_thread, update_sub
    ros_thread = threading.Thread(target=ros_spin, daemon=True)
    ros_thread.start()

    app = omni.kit.app.get_app()
    update_sub = app.get_update_event_stream().create_subscription_to_pop(
        on_update,
        name="go2_dt_full_sync_update",
    )

    builtins._go2_dt_update_sub = update_sub

    print("GO2 DIGITAL TWIN RUNNING")
    print("Debug command available: builtins.go2_print_real_pose()")
    print("Debug command available: builtins.go2_print_anchor()")
    print("Debug command available: builtins.go2_force_anchor_reset()")


main()