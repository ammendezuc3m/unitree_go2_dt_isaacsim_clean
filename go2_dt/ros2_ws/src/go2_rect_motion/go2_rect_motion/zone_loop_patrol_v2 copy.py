#!/usr/bin/env python3

import math
import threading
import time
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Set

import cv2
import numpy as np

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry


# ============================================================
# TERMINAL COLORS
# ============================================================

ANSI_RESET = "\033[0m"
ANSI_RED = "\033[91m"
ANSI_GREEN = "\033[92m"
ANSI_YELLOW = "\033[93m"
ANSI_BLUE = "\033[94m"
ANSI_MAGENTA = "\033[95m"
ANSI_CYAN = "\033[96m"
ANSI_WHITE = "\033[97m"


def col(text: str, color: str) -> str:
    return f"{color}{text}{ANSI_RESET}"


# ============================================================
# MAP CONFIG
# ============================================================

ROWS = 10
COLS = 8
DEFAULT_TILE_SIZE_M = 0.60

STATIC_WALLS: Set[Tuple[int, int]] = {
    (1, 1), (1, 2), (2, 2),
}

BLACK_REFERENCE_PATH: List[Tuple[float, float]] = [
    (3.0, 2.0),
    (3.0, 4.0),
    (0.0, 4.0),
    (0.0, 0.0),
    (3.0, 0.0),
    (3.0, 2.0),
]


# ============================================================
# ROUTE CONFIG
# ============================================================

REQUESTED_START_TILE = (2.0, 4.0)

# Ruta actual ajustable por variables independientes.
# Con esto puedes tocar cada recta sin reescribir toda la ruta.

BASE_DERECHA_X = 2.85
BASE_IZQUIERDA_X = 0.70
BASE_SUPERIOR_Y = 0.45
BASE_INFERIOR_Y = 3.35

EXPAND_M = 0.05
EXPAND_TILES = EXPAND_M / DEFAULT_TILE_SIZE_M

RECTA_DERECHA_X = BASE_DERECHA_X + EXPAND_TILES
RECTA_IZQUIERDA_X = BASE_IZQUIERDA_X - EXPAND_TILES
RECTA_SUPERIOR_Y = BASE_SUPERIOR_Y - EXPAND_TILES
RECTA_INFERIOR_Y = BASE_INFERIOR_Y + EXPAND_TILES

DIAG_SUP_DER_X = 0.24
DIAG_SUP_DER_Y = 0.24

DIAG_INF_DER_X = 0.24
DIAG_INF_DER_Y = 0.24

DIAG_INF_IZQ_X = 0.24
DIAG_INF_IZQ_Y = 0.24

DIAG_SUP_IZQ_X = 0.24
DIAG_SUP_IZQ_Y = 0.24

INITIAL_WP = (REQUESTED_START_TILE[0], RECTA_INFERIOR_Y)
START_TILE = INITIAL_WP


def build_route_points() -> Dict[str, Tuple[float, float]]:
    return {
        "INF_DER_VERTICAL": (
            RECTA_DERECHA_X,
            RECTA_INFERIOR_Y - DIAG_INF_DER_Y,
        ),
        "INF_DER_HORIZONTAL": (
            RECTA_DERECHA_X - DIAG_INF_DER_X,
            RECTA_INFERIOR_Y,
        ),
        "INF_IZQ_HORIZONTAL": (
            RECTA_IZQUIERDA_X + DIAG_INF_IZQ_X,
            RECTA_INFERIOR_Y,
        ),
        "INF_IZQ_VERTICAL": (
            RECTA_IZQUIERDA_X,
            RECTA_INFERIOR_Y - DIAG_INF_IZQ_Y,
        ),
        "SUP_IZQ_VERTICAL": (
            RECTA_IZQUIERDA_X,
            RECTA_SUPERIOR_Y + DIAG_SUP_IZQ_Y,
        ),
        "SUP_IZQ_HORIZONTAL": (
            RECTA_IZQUIERDA_X + DIAG_SUP_IZQ_X,
            RECTA_SUPERIOR_Y,
        ),
        "SUP_DER_HORIZONTAL": (
            RECTA_DERECHA_X - DIAG_SUP_DER_X,
            RECTA_SUPERIOR_Y,
        ),
        "SUP_DER_VERTICAL": (
            RECTA_DERECHA_X,
            RECTA_SUPERIOR_Y + DIAG_SUP_DER_Y,
        ),
    }


ROUTE_POINTS = build_route_points()


def build_loop_route_from_new_start() -> List[Tuple[float, float]]:
    """
    Ruta completa incluyendo parada explícita en INITIAL_WP al cerrar vuelta.

    El perro empieza en INITIAL_WP mirando hacia la izquierda.
    Primer tramo: INITIAL_WP -> INF_IZQ_HORIZONTAL.
    Al final de la vuelta vuelve explícitamente a INITIAL_WP.
    """
    return [
        ROUTE_POINTS["INF_IZQ_HORIZONTAL"],
        ROUTE_POINTS["INF_IZQ_VERTICAL"],
        ROUTE_POINTS["SUP_IZQ_VERTICAL"],
        ROUTE_POINTS["SUP_IZQ_HORIZONTAL"],
        ROUTE_POINTS["SUP_DER_HORIZONTAL"],
        ROUTE_POINTS["SUP_DER_VERTICAL"],
        ROUTE_POINTS["INF_DER_VERTICAL"],
        ROUTE_POINTS["INF_DER_HORIZONTAL"],
        INITIAL_WP,
    ]


LOOP_ROUTE: List[Tuple[float, float]] = build_loop_route_from_new_start()

CHECKPOINTS: List[Tuple[str, Tuple[float, float]]] = [
    ("MID_LEFT", (RECTA_IZQUIERDA_X, 2.00)),
    ("MID_TOP", ((RECTA_IZQUIERDA_X + RECTA_DERECHA_X) / 2.0, RECTA_SUPERIOR_Y)),
    ("MID_BOTTOM", ((RECTA_IZQUIERDA_X + RECTA_DERECHA_X) / 2.0, RECTA_INFERIOR_Y)),
]


# ============================================================
# HELPERS
# ============================================================

def normalize_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


def normalize_angle_deg(angle: float) -> float:
    while angle > 180.0:
        angle -= 360.0
    while angle < -180.0:
        angle += 360.0
    return angle


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class Pose2D:
    x: float
    y: float
    yaw: float
    yaw_rate: float = 0.0


@dataclass
class SegmentDebug:
    from_wp: Tuple[float, float]
    to_wp: Tuple[float, float]
    seg_len: float
    progress: float
    remaining: float
    cross_track: float
    dist_to_target: float
    target_yaw: float
    yaw_error: float
    linear_cmd: float = 0.0
    lateral_cmd: float = 0.0
    angular_cmd: float = 0.0


@dataclass
class AprilTagState:
    detected: bool = False
    calibrated_zero: bool = False
    cx: float = 0.0
    cy: float = 0.0
    yaw_deg: float = 0.0
    target_x: float = 0.0
    target_y: float = 0.0
    target_yaw_deg: float = 0.0
    err_x_px: float = 0.0
    err_y_px: float = 0.0
    err_yaw_deg: float = 0.0
    instant_ok: bool = False
    stable_ok: bool = False
    ok_votes: int = 0
    total_votes: int = 0


@dataclass
class DriftSummary:
    lap: int = 0
    odom_dx_m: float = 0.0
    odom_dy_m: float = 0.0
    odom_dyaw_deg: float = 0.0
    tag_err_x_px: float = 0.0
    tag_err_y_px: float = 0.0
    tag_err_yaw_deg: float = 0.0


# ============================================================
# APRILTAG CAMERA
# ============================================================

class AprilTagCamera:
    def __init__(
        self,
        camera_index: int,
        tag_id: int,
        tol_x_px: float,
        tol_y_px: float,
        tol_yaw_deg: float,
        vote_window: int,
        vote_required: int,
        show_window: bool,
    ):
        self.camera_index = camera_index
        self.tag_id = tag_id

        self.tol_x_px = tol_x_px
        self.tol_y_px = tol_y_px
        self.tol_yaw_deg = tol_yaw_deg

        self.vote_window = vote_window
        self.vote_required = vote_required

        self.show_window = show_window

        self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_V4L2)

        if not self.cap.isOpened():
            raise RuntimeError(f"No se pudo abrir la cámara index={self.camera_index}")

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
        parameters = cv2.aruco.DetectorParameters()
        self.detector = cv2.aruco.ArucoDetector(dictionary, parameters)

        self.zero_set = False
        self.target_x = 0.0
        self.target_y = 0.0
        self.target_yaw_deg = 0.0

        self.last_state = AprilTagState()
        self.ok_history = deque(maxlen=self.vote_window)

        self.last_c_pressed = False

    def close(self):
        try:
            self.cap.release()
        except Exception:
            pass

        try:
            cv2.destroyWindow("AprilTag start calibrator")
        except Exception:
            pass

    def compute_tag_center(self, corners) -> Tuple[float, float]:
        pts = np.array(corners, dtype=np.float32)
        return float(np.mean(pts[:, 0])), float(np.mean(pts[:, 1]))

    def compute_tag_yaw_deg(self, corners) -> float:
        pts = np.array(corners, dtype=np.float32)
        p0 = pts[0]
        p1 = pts[1]

        dx = float(p1[0] - p0[0])
        dy = float(p1[1] - p0[1])

        return normalize_angle_deg(math.degrees(math.atan2(dy, dx)))

    def draw_cross(self, frame, x, y, color, size=14, thickness=2):
        x = int(round(x))
        y = int(round(y))

        cv2.line(frame, (x - size, y), (x + size, y), color, thickness)
        cv2.line(frame, (x, y - size), (x, y + size), color, thickness)

    def set_zero_from_current_tag(self) -> bool:
        if not self.last_state.detected:
            return False

        self.target_x = self.last_state.cx
        self.target_y = self.last_state.cy
        self.target_yaw_deg = self.last_state.yaw_deg
        self.zero_set = True
        self.ok_history.clear()

        return True

    def process_once(self) -> Tuple[AprilTagState, bool]:
        """
        Devuelve:
        - estado AprilTag
        - c_pressed: True si el usuario ha pulsado c en la ventana OpenCV
        """
        ok, frame = self.cap.read()

        if not ok or frame is None:
            self.last_state = AprilTagState(detected=False, calibrated_zero=self.zero_set)
            return self.last_state, False

        h, w = frame.shape[:2]

        if not self.zero_set:
            target_x = w / 2.0
            target_y = h / 2.0
            target_yaw = 0.0
        else:
            target_x = self.target_x
            target_y = self.target_y
            target_yaw = self.target_yaw_deg

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners_list, ids, _ = self.detector.detectMarkers(gray)

        detected = False
        selected_corners = None

        if ids is not None and len(ids) > 0:
            ids_flat = ids.flatten()

            for i, found_id in enumerate(ids_flat):
                if int(found_id) == self.tag_id:
                    selected_corners = corners_list[i][0]
                    detected = True
                    break

        state = AprilTagState(
            detected=detected,
            calibrated_zero=self.zero_set,
            target_x=target_x,
            target_y=target_y,
            target_yaw_deg=target_yaw,
        )

        c_pressed = False

        self.draw_cross(frame, target_x, target_y, (255, 0, 255), size=18, thickness=2)

        if detected and selected_corners is not None:
            cx, cy = self.compute_tag_center(selected_corners)
            yaw_deg = self.compute_tag_yaw_deg(selected_corners)

            err_x = cx - target_x
            err_y = cy - target_y
            err_yaw = normalize_angle_deg(yaw_deg - target_yaw)

            instant_ok = (
                self.zero_set
                and abs(err_x) <= self.tol_x_px
                and abs(err_y) <= self.tol_y_px
                and abs(err_yaw) <= self.tol_yaw_deg
            )

            if self.zero_set:
                self.ok_history.append(instant_ok)
            else:
                self.ok_history.clear()

            ok_votes = sum(1 for v in self.ok_history if v)
            total_votes = len(self.ok_history)
            stable_ok = total_votes >= self.vote_window and ok_votes >= self.vote_required

            state = AprilTagState(
                detected=True,
                calibrated_zero=self.zero_set,
                cx=cx,
                cy=cy,
                yaw_deg=yaw_deg,
                target_x=target_x,
                target_y=target_y,
                target_yaw_deg=target_yaw,
                err_x_px=err_x,
                err_y_px=err_y,
                err_yaw_deg=err_yaw,
                instant_ok=instant_ok,
                stable_ok=stable_ok,
                ok_votes=ok_votes,
                total_votes=total_votes,
            )

            color = (0, 255, 0) if stable_ok else (0, 0, 255)

            pts = selected_corners.astype(int)
            cv2.polylines(frame, [pts], True, color, 3)

            for idx, p in enumerate(pts):
                cv2.circle(frame, tuple(p), 5, (255, 255, 0), -1)
                cv2.putText(
                    frame,
                    str(idx),
                    (int(p[0]) + 5, int(p[1]) + 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (255, 255, 0),
                    2,
                )

            self.draw_cross(frame, cx, cy, color, size=16, thickness=3)
            cv2.line(frame, (int(target_x), int(target_y)), (int(cx), int(cy)), color, 2)

            if not self.zero_set:
                title = "PRESS C TO START"
            elif stable_ok:
                title = "START POSE OK"
            else:
                title = "ALIGNING TO START"

            cv2.putText(frame, title, (30, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 3)

            cv2.putText(
                frame,
                f"err_x={err_x:+.1f}px err_y={err_y:+.1f}px err_yaw={err_yaw:+.1f}deg",
                (30, 80),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
            )

            cv2.putText(
                frame,
                f"votes={ok_votes}/{total_votes} required={self.vote_required}/{self.vote_window}",
                (30, 115),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
            )

            cv2.putText(
                frame,
                f"zero_yaw={target_yaw:+.1f} yaw={yaw_deg:+.1f}",
                (30, 150),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
            )

        else:
            self.ok_history.append(False)

            cv2.putText(
                frame,
                "APRILTAG NOT DETECTED",
                (30, 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (0, 0, 255),
                3,
            )

        if self.show_window:
            cv2.imshow("AprilTag start calibrator", frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("c"):
                c_pressed = True

            if key == ord("q") or key == 27:
                raise KeyboardInterrupt()

        self.last_state = state
        return state, c_pressed


# ============================================================
# NODE
# ============================================================

class SafeInsetPatrolNode(Node):
    def __init__(self):
        super().__init__("safe_inset_patrol_apriltag")

        self.lock = threading.RLock()

        # ---------------- Parameters ----------------
        self.declare_parameter("cmd_topic", "/cmd_vel_out")
        self.declare_parameter("odom_topic", "/odom")

        self.declare_parameter("loop_hz", 25.0)

        self.declare_parameter("linear_speed", 0.070)
        self.declare_parameter("angular_speed", 0.24)

        self.declare_parameter("tile_size", DEFAULT_TILE_SIZE_M)
        self.declare_parameter("settle_time_sec", 0.80)

        self.declare_parameter("corner_overshoot_m", 0.020)
        self.declare_parameter("fixed_turn_tolerance_deg", 3.0)

        # Drift guard durante la ruta.
        self.declare_parameter("enable_drift_guard", True)
        self.declare_parameter("drift_yaw_threshold_deg", 5.0)
        self.declare_parameter("drift_cross_track_threshold_m", 0.070)
        self.declare_parameter("drift_yaw_tolerance_deg", 3.0)
        self.declare_parameter("drift_cross_track_tolerance_m", 0.040)
        self.declare_parameter("drift_correction_speed", 0.030)
        self.declare_parameter("drift_correction_kp", 0.60)
        self.declare_parameter("drift_max_time_sec", 4.0)
        self.declare_parameter("drift_min_progress_margin_m", 0.10)

        # Cámara AprilTag.
        self.declare_parameter("camera_index", 4)
        self.declare_parameter("apriltag_id", 0)
        self.declare_parameter("tag_tol_x_px", 10.0)
        self.declare_parameter("tag_tol_y_px", 10.0)
        self.declare_parameter("tag_tol_yaw_deg", 2.0)
        self.declare_parameter("tag_vote_window", 10)
        self.declare_parameter("tag_vote_required", 8)
        self.declare_parameter("show_camera_window", True)

        # Correcciones discretas por cámara.
        self.declare_parameter("camera_align_stop_sec", 0.60)
        self.declare_parameter("camera_align_pulse_min_sec", 0.18)
        self.declare_parameter("camera_align_pulse_max_sec", 0.55)
        self.declare_parameter("camera_align_linear_speed", 0.035)
        self.declare_parameter("camera_align_lateral_speed", 0.035)
        self.declare_parameter("camera_align_angular_speed", 0.10)

        # Signos de corrección de cámara.
        # Si al corregir se aleja, invierte el signo correspondiente.
        self.declare_parameter("camera_cmd_x_from_err_y_sign", -1.0)
        self.declare_parameter("camera_cmd_y_from_err_x_sign", -1.0)

        # IMPORTANTE:
        # En tus logs, err_yaw positivo con cmd_w negativo hacía crecer el error.
        # Por tanto, el yaw debe corregirse con signo positivo.
        self.declare_parameter("camera_cmd_w_from_err_yaw_sign", 1.0)

        self.declare_parameter("enable_gui", True)
        self.declare_parameter("gui_update_hz", 10.0)

        # Compatibilidad con comandos antiguos.
        self.declare_parameter("require_lidar_guard", False)
        self.declare_parameter("enable_lap_recalibration", False)
        self.declare_parameter("enable_obstacle_reversal", False)
        self.declare_parameter("lidar_person_global_stop", False)
        self.declare_parameter("confirm_blocked_sec", 3.0)
        self.declare_parameter("reverse_cooldown_sec", 0.7)
        self.declare_parameter("lidar_min_alignment_score", 0.70)
        self.declare_parameter("lidar_max_abs_dx_m", 0.35)
        self.declare_parameter("lidar_max_abs_dy_m", 0.35)
        self.declare_parameter("lidar_max_abs_dyaw_deg", 12.0)
        self.declare_parameter("localization_bad_grace_sec", 0.6)
        self.declare_parameter("recalibrate_every_laps", 1)

        # ---------------- Read parameters ----------------
        self.cmd_topic = self.get_parameter("cmd_topic").value
        self.odom_topic = self.get_parameter("odom_topic").value

        self.loop_hz = float(self.get_parameter("loop_hz").value)

        self.linear_speed = float(self.get_parameter("linear_speed").value)
        self.angular_speed = float(self.get_parameter("angular_speed").value)

        self.tile_size = float(self.get_parameter("tile_size").value)
        self.settle_time_sec = float(self.get_parameter("settle_time_sec").value)

        self.corner_overshoot_m = float(self.get_parameter("corner_overshoot_m").value)

        self.fixed_turn_tolerance = math.radians(
            float(self.get_parameter("fixed_turn_tolerance_deg").value)
        )

        self.enable_drift_guard = bool(self.get_parameter("enable_drift_guard").value)
        self.drift_yaw_threshold = math.radians(
            float(self.get_parameter("drift_yaw_threshold_deg").value)
        )
        self.drift_cross_track_threshold_m = float(
            self.get_parameter("drift_cross_track_threshold_m").value
        )
        self.drift_yaw_tolerance = math.radians(
            float(self.get_parameter("drift_yaw_tolerance_deg").value)
        )
        self.drift_cross_track_tolerance_m = float(
            self.get_parameter("drift_cross_track_tolerance_m").value
        )
        self.drift_correction_speed = float(
            self.get_parameter("drift_correction_speed").value
        )
        self.drift_correction_kp = float(
            self.get_parameter("drift_correction_kp").value
        )
        self.drift_max_time_sec = float(
            self.get_parameter("drift_max_time_sec").value
        )
        self.drift_min_progress_margin_m = float(
            self.get_parameter("drift_min_progress_margin_m").value
        )

        self.camera_align_stop_sec = float(
            self.get_parameter("camera_align_stop_sec").value
        )
        self.camera_align_pulse_min_sec = float(
            self.get_parameter("camera_align_pulse_min_sec").value
        )
        self.camera_align_pulse_max_sec = float(
            self.get_parameter("camera_align_pulse_max_sec").value
        )
        self.camera_align_linear_speed = float(
            self.get_parameter("camera_align_linear_speed").value
        )
        self.camera_align_lateral_speed = float(
            self.get_parameter("camera_align_lateral_speed").value
        )
        self.camera_align_angular_speed = float(
            self.get_parameter("camera_align_angular_speed").value
        )

        self.camera_cmd_x_from_err_y_sign = float(
            self.get_parameter("camera_cmd_x_from_err_y_sign").value
        )
        self.camera_cmd_y_from_err_x_sign = float(
            self.get_parameter("camera_cmd_y_from_err_x_sign").value
        )
        self.camera_cmd_w_from_err_yaw_sign = float(
            self.get_parameter("camera_cmd_w_from_err_yaw_sign").value
        )

        self.enable_gui = bool(self.get_parameter("enable_gui").value)
        self.gui_update_hz = float(self.get_parameter("gui_update_hz").value)

        camera_index = int(self.get_parameter("camera_index").value)
        apriltag_id = int(self.get_parameter("apriltag_id").value)
        tag_tol_x = float(self.get_parameter("tag_tol_x_px").value)
        tag_tol_y = float(self.get_parameter("tag_tol_y_px").value)
        tag_tol_yaw = float(self.get_parameter("tag_tol_yaw_deg").value)
        tag_vote_window = int(self.get_parameter("tag_vote_window").value)
        tag_vote_required = int(self.get_parameter("tag_vote_required").value)
        show_camera_window = bool(self.get_parameter("show_camera_window").value)

        self.tag_camera = AprilTagCamera(
            camera_index=camera_index,
            tag_id=apriltag_id,
            tol_x_px=tag_tol_x,
            tol_y_px=tag_tol_y,
            tol_yaw_deg=tag_tol_yaw,
            vote_window=tag_vote_window,
            vote_required=tag_vote_required,
            show_window=show_camera_window,
        )

        # ---------------- ROS ----------------
        self.cmd_pub = self.create_publisher(Twist, self.cmd_topic, 10)
        self.odom_sub = self.create_subscription(Odometry, self.odom_topic, self.odom_callback, 20)
        self.loop_timer = self.create_timer(1.0 / self.loop_hz, self.control_loop)

        # ---------------- Pose / anchor ----------------
        self.current_pose: Optional[Pose2D] = None

        self.anchor_pose: Optional[Pose2D] = None
        self.forward_vec: Optional[Tuple[float, float]] = None
        self.right_vec: Optional[Tuple[float, float]] = None
        self.anchor_generation = 0

        self.initial_odom_pose: Optional[Pose2D] = None

        # ---------------- Route state ----------------
        self.loop_route = list(LOOP_ROUTE)

        self.current_wp = INITIAL_WP
        self.target_index = 0

        # El perro NO se mueve hasta pulsar C con AprilTag detectado.
        self.motion_state = "WAIT_CAMERA_ZERO"
        self.state_enter_time = time.monotonic()

        self.turn_start_yaw: Optional[float] = None
        self.pending_turn_delta: Optional[float] = None

        self.completed_laps = 0
        self.last_debug: Optional[SegmentDebug] = None

        self.drift_started_time: Optional[float] = None

        self.camera_pulse_cmd = Twist()
        self.camera_pulse_end_time = 0.0
        self.last_tag_state = AprilTagState()
        self.last_drift_summary = DriftSummary()

        self.log_event(
            "INIT",
            "Esperando AprilTag. Coloca el perro en posición inicial real y pulsa C en la ventana de cámara.",
            ANSI_GREEN,
            warn=True,
        )

        self.log_event(
            "ROUTE_VARS",
            f"LEFT_X={RECTA_IZQUIERDA_X:.3f} | RIGHT_X={RECTA_DERECHA_X:.3f} | "
            f"TOP_Y={RECTA_SUPERIOR_Y:.3f} | BOTTOM_Y={RECTA_INFERIOR_Y:.3f}",
            ANSI_CYAN,
            warn=True,
        )

    # --------------------------------------------------------
    # Logging
    # --------------------------------------------------------

    def log_event(self, tag: str, msg: str, color: str = ANSI_WHITE, warn: bool = False):
        text = col(f"[{tag}] {msg}", color)
        if warn:
            self.get_logger().warn(text)
        else:
            self.get_logger().info(text)

    # --------------------------------------------------------
    # ROS callbacks
    # --------------------------------------------------------

    def odom_callback(self, msg: Odometry):
        q = msg.pose.pose.orientation

        pose = Pose2D(
            x=float(msg.pose.pose.position.x),
            y=float(msg.pose.pose.position.y),
            yaw=yaw_from_quaternion(q.x, q.y, q.z, q.w),
            yaw_rate=float(msg.twist.twist.angular.z),
        )

        with self.lock:
            self.current_pose = pose

    # --------------------------------------------------------
    # Anchor / transforms
    # --------------------------------------------------------

    def set_anchor_from_pose_and_forward_yaw(self, pose: Pose2D, forward_yaw: float, reason: str):
        self.anchor_pose = Pose2D(
            x=pose.x,
            y=pose.y,
            yaw=forward_yaw,
            yaw_rate=0.0,
        )

        self.forward_vec = (math.cos(forward_yaw), math.sin(forward_yaw))
        self.right_vec = (math.sin(forward_yaw), -math.cos(forward_yaw))
        self.anchor_generation += 1

        self.log_event(
            "ANCHOR",
            f"reason={reason} x={self.anchor_pose.x:.3f} y={self.anchor_pose.y:.3f} "
            f"forward_yaw={math.degrees(forward_yaw):+.1f} deg "
            f"START_TILE={START_TILE} generation={self.anchor_generation}",
            ANSI_BLUE,
            warn=True,
        )

    def reset_logical_start_here(self, reason: str):
        if self.current_pose is None:
            return

        self.set_anchor_from_pose_and_forward_yaw(
            pose=self.current_pose,
            forward_yaw=self.current_pose.yaw,
            reason=reason,
        )

        self.current_wp = INITIAL_WP
        self.target_index = 0
        self.turn_start_yaw = None
        self.pending_turn_delta = None

    def tile_to_world_float(self, tx: float, ty: float) -> Tuple[float, float]:
        if self.anchor_pose is None or self.forward_vec is None or self.right_vec is None:
            raise RuntimeError("Anchor not initialized")

        dx_tiles = START_TILE[0] - tx
        dy_tiles = ty - START_TILE[1]

        dx_m = dx_tiles * self.tile_size
        dy_m = dy_tiles * self.tile_size

        wx = self.anchor_pose.x + dx_m * self.forward_vec[0] + dy_m * self.right_vec[0]
        wy = self.anchor_pose.y + dx_m * self.forward_vec[1] + dy_m * self.right_vec[1]

        return wx, wy

    def world_to_tile_float(self, x: float, y: float) -> Tuple[float, float]:
        if self.anchor_pose is None or self.forward_vec is None or self.right_vec is None:
            raise RuntimeError("Anchor not initialized")

        dx = x - self.anchor_pose.x
        dy = y - self.anchor_pose.y

        proj_forward = dx * self.forward_vec[0] + dy * self.forward_vec[1]
        proj_right = dx * self.right_vec[0] + dy * self.right_vec[1]

        tx = START_TILE[0] - (proj_forward / self.tile_size)
        ty = START_TILE[1] + (proj_right / self.tile_size)

        return tx, ty

    def current_snapped_tile(self) -> Optional[Tuple[int, int]]:
        if self.current_pose is None or self.anchor_pose is None:
            return None

        tx_f, ty_f = self.world_to_tile_float(self.current_pose.x, self.current_pose.y)
        tx = int(round(tx_f))
        ty = int(round(ty_f))

        if 0 <= tx < COLS and 0 <= ty < ROWS:
            return tx, ty

        return None

    # --------------------------------------------------------
    # State helpers
    # --------------------------------------------------------

    def set_state(self, new_state: str):
        old = self.motion_state
        self.motion_state = new_state
        self.state_enter_time = time.monotonic()
        self.log_event("STATE", f"{old} -> {new_state}", ANSI_YELLOW)

    def state_elapsed(self) -> float:
        return time.monotonic() - self.state_enter_time

    def target_wp(self) -> Tuple[float, float]:
        return self.loop_route[self.target_index]

    def full_draw_route(self) -> List[Tuple[float, float]]:
        return [INITIAL_WP] + list(self.loop_route)

    def heading_between_waypoints(
        self,
        from_wp: Tuple[float, float],
        to_wp: Tuple[float, float],
    ) -> float:
        sx, sy = self.tile_to_world_float(from_wp[0], from_wp[1])
        tx, ty = self.tile_to_world_float(to_wp[0], to_wp[1])
        return math.atan2(ty - sy, tx - sx)

    def advance_segment(self):
        old_from = self.current_wp
        old_target = self.target_wp()

        old_heading = self.heading_between_waypoints(old_from, old_target)

        self.current_wp = old_target
        self.target_index += 1

        lap_finished = False

        if self.target_index >= len(self.loop_route):
            self.target_index = 0
            self.completed_laps += 1
            lap_finished = True

        if lap_finished:
            self.publish_zero()
            self.set_state("CAMERA_ALIGN_STOP")

            self.log_event(
                "LAP",
                f"completed_laps={self.completed_laps}. Entrando en calibración por cámara.",
                ANSI_GREEN,
                warn=True,
            )

            return

        new_target = self.target_wp()
        new_heading = self.heading_between_waypoints(self.current_wp, new_target)

        self.pending_turn_delta = normalize_angle(new_heading - old_heading)
        self.turn_start_yaw = None

        self.publish_zero()
        self.set_state("STOP_BEFORE_TURN")

        self.log_event(
            "CORNER",
            f"reached={old_target} | new_from={self.current_wp} | new_to={new_target} | "
            f"fixed_turn_delta={math.degrees(self.pending_turn_delta):+.1f} deg",
            ANSI_CYAN,
            warn=True,
        )

    # --------------------------------------------------------
    # Segment geometry
    # --------------------------------------------------------

    def compute_segment_debug(self) -> SegmentDebug:
        if self.current_pose is None:
            raise RuntimeError("No current pose")

        from_wp = self.current_wp
        to_wp = self.target_wp()

        sx, sy = self.tile_to_world_float(from_wp[0], from_wp[1])
        tx, ty = self.tile_to_world_float(to_wp[0], to_wp[1])

        vx = tx - sx
        vy = ty - sy
        seg_len = math.hypot(vx, vy)

        if seg_len < 1e-9:
            raise RuntimeError(f"Invalid zero segment {from_wp}->{to_wp}")

        ux = vx / seg_len
        uy = vy / seg_len

        rx = self.current_pose.x - sx
        ry = self.current_pose.y - sy

        progress = rx * ux + ry * uy
        remaining = seg_len - progress

        cross_track = rx * uy - ry * ux

        dist_to_target = math.hypot(tx - self.current_pose.x, ty - self.current_pose.y)

        target_yaw = math.atan2(vy, vx)
        yaw_error = normalize_angle(target_yaw - self.current_pose.yaw)

        return SegmentDebug(
            from_wp=from_wp,
            to_wp=to_wp,
            seg_len=seg_len,
            progress=progress,
            remaining=remaining,
            cross_track=cross_track,
            dist_to_target=dist_to_target,
            target_yaw=target_yaw,
            yaw_error=yaw_error,
        )

    def corner_reached(self, dbg: SegmentDebug) -> bool:
        return dbg.remaining <= -self.corner_overshoot_m

    # --------------------------------------------------------
    # Drift guard
    # --------------------------------------------------------

    def projected_point_on_current_segment(self, dbg: SegmentDebug) -> Tuple[float, float]:
        sx, sy = self.tile_to_world_float(dbg.from_wp[0], dbg.from_wp[1])
        tx, ty = self.tile_to_world_float(dbg.to_wp[0], dbg.to_wp[1])

        vx = tx - sx
        vy = ty - sy
        seg_len = math.hypot(vx, vy)

        ux = vx / seg_len
        uy = vy / seg_len

        clamped_progress = clamp(dbg.progress, 0.0, dbg.seg_len)

        px = sx + clamped_progress * ux
        py = sy + clamped_progress * uy

        return px, py

    def body_error_to_world_point(self, wx: float, wy: float) -> Tuple[float, float]:
        if self.current_pose is None:
            return 0.0, 0.0

        dx = wx - self.current_pose.x
        dy = wy - self.current_pose.y

        yaw = self.current_pose.yaw

        err_x_body = math.cos(yaw) * dx + math.sin(yaw) * dy
        err_y_body = -math.sin(yaw) * dx + math.cos(yaw) * dy

        return err_x_body, err_y_body

    def drift_guard_should_trigger(self, dbg: SegmentDebug) -> Tuple[bool, str]:
        if not self.enable_drift_guard:
            return False, ""

        if dbg.progress < self.drift_min_progress_margin_m:
            return False, ""

        if dbg.remaining < self.drift_min_progress_margin_m:
            return False, ""

        yaw_bad = abs(dbg.yaw_error) > self.drift_yaw_threshold
        cte_bad = abs(dbg.cross_track) > self.drift_cross_track_threshold_m

        if yaw_bad and cte_bad:
            return True, "yaw_and_cross_track"
        if yaw_bad:
            return True, "yaw"
        if cte_bad:
            return True, "cross_track"

        return False, ""

    def drift_is_good_enough(self, dbg: SegmentDebug) -> bool:
        return (
            abs(dbg.yaw_error) <= self.drift_yaw_tolerance
            and abs(dbg.cross_track) <= self.drift_cross_track_tolerance_m
        )

    def maybe_enter_drift_guard(self, dbg: SegmentDebug) -> bool:
        should, reason = self.drift_guard_should_trigger(dbg)

        if not should:
            return False

        self.drift_started_time = None
        self.publish_zero()
        self.set_state("DRIFT_STOP")

        self.log_event(
            "DRIFT",
            f"ENTER reason={reason} | cte={dbg.cross_track:+.3f} m | "
            f"yaw_err={math.degrees(dbg.yaw_error):+.1f} deg | "
            f"segment={dbg.from_wp}->{dbg.to_wp}",
            ANSI_RED,
            warn=True,
        )

        return True

    def handle_drift_stop(self) -> bool:
        if self.motion_state != "DRIFT_STOP":
            return False

        self.publish_zero()

        if self.state_elapsed() >= self.settle_time_sec:
            self.drift_started_time = time.monotonic()
            self.set_state("DRIFT_CORRECT")

        return True

    def handle_drift_correct(self, dbg: SegmentDebug) -> bool:
        if self.motion_state != "DRIFT_CORRECT":
            return False

        elapsed = time.monotonic() - (self.drift_started_time or time.monotonic())

        if self.drift_is_good_enough(dbg):
            self.publish_zero()
            self.drift_started_time = None
            self.set_state("DRIFT_SETTLE")

            self.log_event(
                "DRIFT",
                f"CORRECTED OK | cte={dbg.cross_track:+.3f} m | "
                f"yaw_err={math.degrees(dbg.yaw_error):+.1f} deg",
                ANSI_GREEN,
                warn=True,
            )
            return True

        if elapsed >= self.drift_max_time_sec:
            self.publish_zero()
            self.drift_started_time = None
            self.set_state("DRIFT_SETTLE")

            self.log_event(
                "DRIFT",
                f"TIMEOUT | continuing | cte={dbg.cross_track:+.3f} m | "
                f"yaw_err={math.degrees(dbg.yaw_error):+.1f} deg",
                ANSI_RED,
                warn=True,
            )
            return True

        projected_x, projected_y = self.projected_point_on_current_segment(dbg)
        err_x_body, err_y_body = self.body_error_to_world_point(projected_x, projected_y)

        cmd = Twist()

        if abs(dbg.yaw_error) > self.drift_yaw_tolerance:
            cmd.angular.z = 0.30 * self.angular_speed if dbg.yaw_error > 0.0 else -0.30 * self.angular_speed
            cmd.linear.x = 0.0
            cmd.linear.y = 0.0

            self.log_event(
                "DRIFT-YAW",
                f"yaw_err={math.degrees(dbg.yaw_error):+.1f} deg | cmd_w={cmd.angular.z:+.3f}",
                ANSI_YELLOW,
                warn=True,
            )
        else:
            vx = 0.0
            vy = 0.0

            if abs(err_y_body) > self.drift_cross_track_tolerance_m:
                vy = self.drift_correction_kp * err_y_body
                vy = clamp(vy, -self.drift_correction_speed, self.drift_correction_speed)

            if abs(err_x_body) > 0.08:
                vx = self.drift_correction_kp * err_x_body
                vx = clamp(vx, -self.drift_correction_speed, self.drift_correction_speed)

            cmd.linear.x = vx
            cmd.linear.y = vy
            cmd.angular.z = 0.0

            self.log_event(
                "DRIFT-POS",
                f"cte={dbg.cross_track:+.3f} | err_x={err_x_body:+.3f} | "
                f"err_y={err_y_body:+.3f} | cmd_y={cmd.linear.y:+.3f}",
                ANSI_MAGENTA,
                warn=True,
            )

        self.cmd_pub.publish(cmd)
        return True

    def handle_drift_settle(self) -> bool:
        if self.motion_state != "DRIFT_SETTLE":
            return False

        self.publish_zero()

        if self.state_elapsed() >= self.settle_time_sec:
            self.set_state("DRIVE")

        return True

    # --------------------------------------------------------
    # AprilTag alignment
    # --------------------------------------------------------

    def compute_camera_pulse_from_tag(self, tag: AprilTagState) -> Twist:
        cmd = Twist()

        # Prioridad:
        # 1) yaw si está mal;
        # 2) error lateral/longitudinal si yaw está razonable.

        if abs(tag.err_yaw_deg) > self.tag_camera.tol_yaw_deg:
            direction = self.camera_cmd_w_from_err_yaw_sign * (1.0 if tag.err_yaw_deg > 0.0 else -1.0)

            cmd.angular.z = direction * self.camera_align_angular_speed
            cmd.linear.x = 0.0
            cmd.linear.y = 0.0
            return cmd

        # Imagen:
        # err_x_px -> desplazamiento horizontal en cámara -> cmd.linear.y
        # err_y_px -> desplazamiento vertical en cámara -> cmd.linear.x
        if abs(tag.err_y_px) > self.tag_camera.tol_y_px:
            direction_x = self.camera_cmd_x_from_err_y_sign * (1.0 if tag.err_y_px > 0.0 else -1.0)
            cmd.linear.x = direction_x * self.camera_align_linear_speed

        if abs(tag.err_x_px) > self.tag_camera.tol_x_px:
            direction_y = self.camera_cmd_y_from_err_x_sign * (1.0 if tag.err_x_px > 0.0 else -1.0)
            cmd.linear.y = direction_y * self.camera_align_lateral_speed

        cmd.angular.z = 0.0
        return cmd

    def compute_camera_pulse_duration(self, tag: AprilTagState) -> float:
        err_px = max(abs(tag.err_x_px), abs(tag.err_y_px))
        err_yaw = abs(tag.err_yaw_deg)

        # Duración corta y discreta. No se corrige en continuo.
        if err_yaw > self.tag_camera.tol_yaw_deg:
            raw = 0.10 + 0.025 * err_yaw
        else:
            raw = 0.12 + 0.006 * err_px

        return clamp(
            raw,
            self.camera_align_pulse_min_sec,
            self.camera_align_pulse_max_sec,
        )

    def update_drift_summary(self, tag: AprilTagState):
        if self.initial_odom_pose is None or self.current_pose is None:
            return

        dx = self.current_pose.x - self.initial_odom_pose.x
        dy = self.current_pose.y - self.initial_odom_pose.y
        dyaw = normalize_angle(self.current_pose.yaw - self.initial_odom_pose.yaw)

        self.last_drift_summary = DriftSummary(
            lap=self.completed_laps,
            odom_dx_m=dx,
            odom_dy_m=dy,
            odom_dyaw_deg=math.degrees(dyaw),
            tag_err_x_px=tag.err_x_px,
            tag_err_y_px=tag.err_y_px,
            tag_err_yaw_deg=tag.err_yaw_deg,
        )

    def handle_wait_camera_zero(self, tag: AprilTagState, c_pressed: bool) -> bool:
        if self.motion_state != "WAIT_CAMERA_ZERO":
            return False

        self.publish_zero()

        if c_pressed:
            if not tag.detected:
                self.log_event(
                    "APRILTAG",
                    "Has pulsado C, pero no hay AprilTag detectado.",
                    ANSI_RED,
                    warn=True,
                )
                return True

            ok = self.tag_camera.set_zero_from_current_tag()

            if not ok:
                self.log_event(
                    "APRILTAG",
                    "No se pudo fijar cero visual.",
                    ANSI_RED,
                    warn=True,
                )
                return True

            if self.current_pose is None:
                self.log_event(
                    "APRILTAG",
                    "Cero visual fijado, pero todavía no hay odometría.",
                    ANSI_RED,
                    warn=True,
                )
                return True

            self.initial_odom_pose = Pose2D(
                self.current_pose.x,
                self.current_pose.y,
                self.current_pose.yaw,
                self.current_pose.yaw_rate,
            )

            self.reset_logical_start_here(reason="external_apriltag_initial_zero")

            self.log_event(
                "APRILTAG",
                f"ZERO SET | x={tag.cx:.1f}px y={tag.cy:.1f}px yaw={tag.yaw_deg:+.1f}deg | "
                f"odom_x={self.current_pose.x:+.3f} odom_y={self.current_pose.y:+.3f} "
                f"odom_yaw={math.degrees(self.current_pose.yaw):+.1f}deg",
                ANSI_GREEN,
                warn=True,
            )

            self.set_state("DRIVE")

        return True

    def handle_camera_align_stop(self) -> bool:
        if self.motion_state != "CAMERA_ALIGN_STOP":
            return False

        self.publish_zero()

        if self.state_elapsed() >= self.camera_align_stop_sec:
            self.set_state("CAMERA_ALIGN_EVALUATE")

        return True

    def handle_camera_align_evaluate(self, tag: AprilTagState) -> bool:
        if self.motion_state != "CAMERA_ALIGN_EVALUATE":
            return False

        self.publish_zero()

        if not tag.detected:
            self.log_event(
                "APRILTAG",
                "No detectado en zona inicial. Esperando sin mover.",
                ANSI_RED,
                warn=True,
            )
            return True

        if tag.stable_ok:
            self.update_drift_summary(tag)

            self.log_event(
                "APRILTAG",
                f"START OK {tag.ok_votes}/{tag.total_votes}. "
                f"Drift lap={self.last_drift_summary.lap} | "
                f"odom_dx={self.last_drift_summary.odom_dx_m:+.3f}m "
                f"odom_dy={self.last_drift_summary.odom_dy_m:+.3f}m "
                f"odom_dyaw={self.last_drift_summary.odom_dyaw_deg:+.1f}deg | "
                f"tag_err=({tag.err_x_px:+.1f}px,{tag.err_y_px:+.1f}px,{tag.err_yaw_deg:+.1f}deg)",
                ANSI_GREEN,
                warn=True,
            )

            # Reset lógico: aunque /odom haya derivado, esta pose real vuelve a ser la inicial.
            self.reset_logical_start_here(reason=f"apriltag_lap_{self.completed_laps}_logical_reset")

            self.set_state("DRIVE")
            return True

        if abs(tag.err_yaw_deg) > 35.0:
            self.publish_zero()
            self.log_event(
                "APRILTAG",
                f"YAW DIVERGED | err_yaw={tag.err_yaw_deg:+.1f}deg. "
                f"Stopping alignment to avoid wrong spinning.",
                ANSI_RED,
                warn=True,
            )
            self.set_state("CAMERA_ALIGN_STOP")
            return True
            
        cmd = self.compute_camera_pulse_from_tag(tag)
        duration = self.compute_camera_pulse_duration(tag)

        self.camera_pulse_cmd = cmd
        self.camera_pulse_end_time = time.monotonic() + duration

        self.log_event(
            "APRILTAG",
            f"ALIGN pulse | votes={tag.ok_votes}/{tag.total_votes} | "
            f"err_x={tag.err_x_px:+.1f}px err_y={tag.err_y_px:+.1f}px "
            f"err_yaw={tag.err_yaw_deg:+.1f}deg | "
            f"cmd_x={cmd.linear.x:+.3f} cmd_y={cmd.linear.y:+.3f} cmd_w={cmd.angular.z:+.3f} "
            f"duration={duration:.2f}s",
            ANSI_MAGENTA,
            warn=True,
        )

        self.set_state("CAMERA_ALIGN_PULSE")
        return True

    def handle_camera_align_pulse(self) -> bool:
        if self.motion_state != "CAMERA_ALIGN_PULSE":
            return False

        if time.monotonic() < self.camera_pulse_end_time:
            self.cmd_pub.publish(self.camera_pulse_cmd)
            return True

        self.publish_zero()
        self.set_state("CAMERA_ALIGN_AFTER_PULSE_STOP")
        return True

    def handle_camera_align_after_pulse_stop(self) -> bool:
        if self.motion_state != "CAMERA_ALIGN_AFTER_PULSE_STOP":
            return False

        self.publish_zero()

        if self.state_elapsed() >= self.camera_align_stop_sec:
            self.set_state("CAMERA_ALIGN_EVALUATE")

        return True

    # --------------------------------------------------------
    # Main control
    # --------------------------------------------------------

    def control_loop(self):
        with self.lock:
            try:
                tag, c_pressed = self.tag_camera.process_once()
                self.last_tag_state = tag
            except KeyboardInterrupt:
                rclpy.shutdown()
                return
            except Exception as e:
                self.log_event("APRILTAG", f"Camera error: {e}", ANSI_RED, warn=True)
                tag = AprilTagState(detected=False)
                c_pressed = False

            if self.handle_wait_camera_zero(tag, c_pressed):
                return

            if self.current_pose is None or self.anchor_pose is None:
                self.get_logger().info("Waiting for odometry/anchor...", throttle_duration_sec=2.0)
                self.publish_zero()
                return

            if self.handle_camera_align_stop():
                return

            if self.handle_camera_align_evaluate(tag):
                return

            if self.handle_camera_align_pulse():
                return

            if self.handle_camera_align_after_pulse_stop():
                return

            try:
                dbg = self.compute_segment_debug()
            except Exception as e:
                self.get_logger().warn(col(f"[CTRL] geometry error: {e}", ANSI_RED), throttle_duration_sec=1.0)
                self.publish_zero()
                return

            if self.handle_drift_stop():
                self.last_debug = dbg
                return

            if self.handle_drift_correct(dbg):
                self.last_debug = dbg
                return

            if self.handle_drift_settle():
                self.last_debug = dbg
                return

            if self.motion_state == "STOP_BEFORE_TURN":
                self.publish_zero()

                if self.state_elapsed() >= self.settle_time_sec:
                    if self.pending_turn_delta is None:
                        self.pending_turn_delta = normalize_angle(dbg.target_yaw - self.current_pose.yaw)

                    self.turn_start_yaw = self.current_pose.yaw
                    self.set_state("ROTATE")

                    self.log_event(
                        "ROTATE",
                        f"fixed relative turn | delta={math.degrees(self.pending_turn_delta):+.1f} deg | "
                        f"start_yaw={math.degrees(self.turn_start_yaw):+.1f} deg",
                        ANSI_CYAN,
                    )

                self.last_debug = dbg
                return

            if self.motion_state == "ROTATE":
                cmd = Twist()

                if self.turn_start_yaw is None:
                    self.turn_start_yaw = self.current_pose.yaw

                if self.pending_turn_delta is None:
                    self.pending_turn_delta = normalize_angle(dbg.target_yaw - self.current_pose.yaw)

                turned = normalize_angle(self.current_pose.yaw - self.turn_start_yaw)
                turn_error = normalize_angle(self.pending_turn_delta - turned)

                dbg.yaw_error = turn_error

                if abs(turn_error) <= self.fixed_turn_tolerance:
                    self.publish_zero()
                    self.pending_turn_delta = None
                    self.turn_start_yaw = None
                    self.set_state("STOP_AFTER_TURN")
                    self.last_debug = dbg

                    self.log_event(
                        "ROTATE",
                        f"completed | residual_error={math.degrees(turn_error):+.1f} deg",
                        ANSI_GREEN,
                    )
                    return

                if abs(turn_error) > math.radians(15.0):
                    cmd.angular.z = self.angular_speed if turn_error > 0.0 else -self.angular_speed
                else:
                    cmd.angular.z = 0.45 * self.angular_speed if turn_error > 0.0 else -0.45 * self.angular_speed

                cmd.linear.x = 0.0
                cmd.linear.y = 0.0

                dbg.linear_cmd = 0.0
                dbg.lateral_cmd = 0.0
                dbg.angular_cmd = cmd.angular.z
                self.last_debug = dbg

                self.log_event(
                    "ROTATE",
                    f"turned={math.degrees(turned):+.1f} deg | "
                    f"target_delta={math.degrees(self.pending_turn_delta):+.1f} deg | "
                    f"error={math.degrees(turn_error):+.1f} deg | "
                    f"cmd_w={cmd.angular.z:+.3f}",
                    ANSI_CYAN,
                )

                self.cmd_pub.publish(cmd)
                return

            if self.motion_state == "STOP_AFTER_TURN":
                self.publish_zero()

                if self.state_elapsed() >= self.settle_time_sec:
                    self.set_state("DRIVE")

                self.last_debug = dbg
                return

            if self.motion_state == "DRIVE":
                if self.maybe_enter_drift_guard(dbg):
                    self.last_debug = dbg
                    return

                if self.corner_reached(dbg):
                    self.advance_segment()
                    self.last_debug = dbg
                    return

                cmd = Twist()
                cmd.linear.x = self.linear_speed
                cmd.linear.y = 0.0
                cmd.angular.z = 0.0

                dbg.linear_cmd = cmd.linear.x
                dbg.lateral_cmd = cmd.linear.y
                dbg.angular_cmd = cmd.angular.z
                self.last_debug = dbg

                self.log_event(
                    "DRIVE_OK",
                    f"{dbg.from_wp}->{dbg.to_wp} | "
                    f"progress={dbg.progress:.3f}/{dbg.seg_len:.3f} | "
                    f"remaining={dbg.remaining:.3f} | dist_target={dbg.dist_to_target:.3f} | "
                    f"cte={dbg.cross_track:+.3f} | "
                    f"yaw_err={math.degrees(dbg.yaw_error):+.1f} | "
                    f"cmd_x={cmd.linear.x:.3f}",
                    ANSI_WHITE,
                )

                self.cmd_pub.publish(cmd)
                return

            self.get_logger().warn(col(f"[UNKNOWN] motion_state={self.motion_state}. Stopping.", ANSI_RED))
            self.publish_zero()

    def publish_zero(self):
        try:
            if rclpy.ok():
                self.cmd_pub.publish(Twist())
        except Exception:
            pass

    # --------------------------------------------------------
    # GUI snapshot
    # --------------------------------------------------------

    def get_gui_snapshot(self) -> Dict:
        with self.lock:
            pose = self.current_pose
            anchor = self.anchor_pose

            if pose is not None and anchor is not None:
                try:
                    tile_float = self.world_to_tile_float(pose.x, pose.y)
                except Exception:
                    tile_float = None

                try:
                    snapped_tile = self.current_snapped_tile()
                except Exception:
                    snapped_tile = None
            else:
                tile_float = None
                snapped_tile = None

            return {
                "pose": pose,
                "anchor": anchor,
                "tile_float": tile_float,
                "snapped_tile": snapped_tile,
                "black_route": list(BLACK_REFERENCE_PATH),
                "orange_route": self.full_draw_route(),
                "current_wp": self.current_wp,
                "target_wp": self.target_wp(),
                "motion_state": self.motion_state,
                "completed_laps": self.completed_laps,
                "anchor_generation": self.anchor_generation,
                "debug": self.last_debug,
                "requested_start": REQUESTED_START_TILE,
                "real_initial_wp": INITIAL_WP,
                "start_tile": START_TILE,
                "tag": self.last_tag_state,
                "drift_summary": self.last_drift_summary,
                "route_vars": {
                    "RECTA_DERECHA_X": RECTA_DERECHA_X,
                    "RECTA_IZQUIERDA_X": RECTA_IZQUIERDA_X,
                    "RECTA_SUPERIOR_Y": RECTA_SUPERIOR_Y,
                    "RECTA_INFERIOR_Y": RECTA_INFERIOR_Y,
                },
            }

    def destroy_node(self):
        self.publish_zero()
        try:
            self.tag_camera.close()
        except Exception:
            pass
        super().destroy_node()


# ============================================================
# TKINTER VISUALIZER
# ============================================================

class PatrolVisualizer:
    def __init__(self, node: SafeInsetPatrolNode):
        self.node = node

        self.root = tk.Tk()
        self.root.title("Go2 Safe Inset Patrol - AprilTag Start Reset")

        self.cell = 58
        self.margin = 40
        self.grid_w = COLS * self.cell
        self.grid_h = ROWS * self.cell

        self.canvas_w = self.grid_w + 2 * self.margin + 620
        self.canvas_h = self.grid_h + 2 * self.margin

        self.canvas = tk.Canvas(
            self.root,
            width=self.canvas_w,
            height=self.canvas_h,
            bg="#f8f8f8",
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.closed = False
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.update_ms = int(1000.0 / max(1.0, self.node.gui_update_hz))
        self.update()

    def on_close(self):
        self.closed = True

        try:
            self.node.publish_zero()
        except Exception:
            pass

        try:
            self.root.destroy()
        except Exception:
            pass

        try:
            rclpy.shutdown()
        except Exception:
            pass

    def tile_float_to_px(self, tx: float, ty: float) -> Tuple[float, float]:
        px = self.margin + tx * self.cell + self.cell / 2.0
        py = self.margin + ty * self.cell + self.cell / 2.0
        return px, py

    def draw_grid(self):
        for y in range(ROWS):
            for x in range(COLS):
                tile = (x, y)

                x0 = self.margin + x * self.cell
                y0 = self.margin + y * self.cell
                x1 = x0 + self.cell
                y1 = y0 + self.cell

                fill = "#ffffff"

                if tile in {(0, 4), (0, 3), (0, 2), (0, 1), (0, 0)}:
                    fill = "#fff0d9"
                if tile in {(1, 0), (2, 0), (3, 0)}:
                    fill = "#e7f8e7"
                if tile in {(3, 1), (3, 2), (3, 3), (3, 4), (2, 4), (1, 4)}:
                    fill = "#e9f2ff"
                if tile in STATIC_WALLS:
                    fill = "#444444"

                self.canvas.create_rectangle(
                    x0,
                    y0,
                    x1,
                    y1,
                    fill=fill,
                    outline="#c7c7c7",
                    width=1,
                )

                label_color = "#ffffff" if tile in STATIC_WALLS else "#777777"

                self.canvas.create_text(
                    x0 + 5,
                    y0 + 5,
                    anchor="nw",
                    text=f"{tile}",
                    font=("Consolas", 8),
                    fill=label_color,
                )

    def draw_polyline(
        self,
        points: List[Tuple[float, float]],
        color: str,
        width: int,
        dash=None,
        closed=True,
    ):
        if len(points) < 2:
            return

        pts = points + [points[0]] if closed else points

        for i in range(len(pts) - 1):
            ax, ay = self.tile_float_to_px(pts[i][0], pts[i][1])
            bx, by = self.tile_float_to_px(pts[i + 1][0], pts[i + 1][1])

            kwargs = {
                "fill": color,
                "width": width,
            }

            if dash is not None:
                kwargs["dash"] = dash

            self.canvas.create_line(ax, ay, bx, by, **kwargs)

    def draw_safety_band(self, points: List[Tuple[float, float]]):
        if len(points) < 2:
            return

        pts = points + [points[0]]

        for width, color in [(24, "#66ff66"), (14, "#ccffcc")]:
            for i in range(len(pts) - 1):
                ax, ay = self.tile_float_to_px(pts[i][0], pts[i][1])
                bx, by = self.tile_float_to_px(pts[i + 1][0], pts[i + 1][1])

                self.canvas.create_line(
                    ax,
                    ay,
                    bx,
                    by,
                    fill=color,
                    width=width,
                    capstyle=tk.ROUND,
                    joinstyle=tk.ROUND,
                )

    def draw_points(self, points: List[Tuple[float, float]], color: str):
        for p in points:
            x, y = self.tile_float_to_px(p[0], p[1])
            self.canvas.create_oval(
                x - 6,
                y - 6,
                x + 6,
                y + 6,
                fill=color,
                outline="#111111",
                width=1,
            )

    def draw_special_start(self, snap: Dict):
        requested = snap["requested_start"]
        real_start = snap["real_initial_wp"]

        rx, ry = self.tile_float_to_px(requested[0], requested[1])
        sx, sy = self.tile_float_to_px(real_start[0], real_start[1])

        self.canvas.create_rectangle(
            rx - 12,
            ry - 12,
            rx + 12,
            ry + 12,
            outline="#00aa00",
            width=3,
        )

        self.canvas.create_text(
            rx,
            ry + 22,
            text="REQ START",
            font=("Consolas", 8, "bold"),
            fill="#00aa00",
        )

        self.canvas.create_rectangle(
            sx - 14,
            sy - 14,
            sx + 14,
            sy + 14,
            outline="#ff8800",
            width=3,
        )

        self.canvas.create_text(
            sx,
            sy - 22,
            text="REAL START",
            font=("Consolas", 8, "bold"),
            fill="#ff8800",
        )

    def draw_current_target(self, snap: Dict):
        current_wp = snap["current_wp"]
        target_wp = snap["target_wp"]

        cx, cy = self.tile_float_to_px(current_wp[0], current_wp[1])
        tx, ty = self.tile_float_to_px(target_wp[0], target_wp[1])

        self.canvas.create_oval(
            tx - 8,
            ty - 8,
            tx + 8,
            ty + 8,
            outline="#ff0000",
            width=3,
        )

        self.canvas.create_oval(
            cx - 8,
            cy - 8,
            cx + 8,
            cy + 8,
            outline="#005eff",
            width=3,
        )

    def draw_dog(self, snap: Dict):
        pose: Optional[Pose2D] = snap["pose"]
        tile_float = snap["tile_float"]

        if pose is None or tile_float is None:
            return

        px, py = self.tile_float_to_px(tile_float[0], tile_float[1])

        r = 13

        self.canvas.create_oval(
            px - r,
            py - r,
            px + r,
            py + r,
            fill="#005eff",
            outline="#111111",
            width=2,
        )

        try:
            ahead_x = pose.x + math.cos(pose.yaw) * 0.25
            ahead_y = pose.y + math.sin(pose.yaw) * 0.25
            ahead_tile = self.node.world_to_tile_float(ahead_x, ahead_y)
            ax, ay = self.tile_float_to_px(ahead_tile[0], ahead_tile[1])

            vx = ax - px
            vy = ay - py
            n = math.hypot(vx, vy)

            if n > 1e-9:
                ax = px + 35.0 * vx / n
                ay = py + 35.0 * vy / n

            self.canvas.create_line(
                px,
                py,
                ax,
                ay,
                fill="#005eff",
                width=4,
                arrow=tk.LAST,
                arrowshape=(14, 16, 7),
            )

        except Exception:
            pass

        self.canvas.create_text(
            px,
            py + 24,
            text="GO2",
            font=("Consolas", 9, "bold"),
            fill="#005eff",
        )

    def draw_text_panel(self, snap: Dict):
        x0 = self.margin + self.grid_w + 30
        y = self.margin

        pose: Optional[Pose2D] = snap["pose"]
        anchor: Optional[Pose2D] = snap["anchor"]
        tile_float = snap["tile_float"]
        dbg: Optional[SegmentDebug] = snap["debug"]
        rv = snap["route_vars"]
        tag: AprilTagState = snap["tag"]
        drift: DriftSummary = snap["drift_summary"]

        lines = []

        lines.append("SAFE PATROL + APRILTAG")
        lines.append("-------------------------")
        lines.append("Press C in camera window")
        lines.append("to set real start pose.")
        lines.append("")

        lines.append(f"Requested start: {snap['requested_start']}")
        lines.append(f"Real initial WP: {snap['real_initial_wp']}")
        lines.append(f"START_TILE: {snap['start_tile']}")

        if pose is None:
            lines.append("Pose: waiting odom")
        else:
            lines.append(f"World x,y: {pose.x:+.3f}, {pose.y:+.3f} m")
            lines.append(f"Yaw: {math.degrees(pose.yaw):+.1f} deg")
            lines.append(f"Yaw rate: {pose.yaw_rate:+.3f} rad/s")

        if tile_float is None:
            lines.append("Grid float: n/a")
        else:
            lines.append(f"Grid float: x={tile_float[0]:+.2f}, y={tile_float[1]:+.2f}")

        lines.append(f"Snapped tile: {snap['snapped_tile']}")
        lines.append(f"Current wp: {snap['current_wp']}")
        lines.append(f"Target wp: {snap['target_wp']}")
        lines.append(f"Motion: {snap['motion_state']}")
        lines.append(f"Laps: {snap['completed_laps']}")

        lines.append("")
        lines.append("APRILTAG")
        lines.append("-------------------------")
        lines.append(f"Detected: {tag.detected}")
        lines.append(f"Zero set: {tag.calibrated_zero}")
        lines.append(f"err_x: {tag.err_x_px:+.1f}px")
        lines.append(f"err_y: {tag.err_y_px:+.1f}px")
        lines.append(f"err_yaw: {tag.err_yaw_deg:+.1f} deg")
        lines.append(f"instant_ok: {tag.instant_ok}")
        lines.append(f"stable_ok: {tag.stable_ok}")
        lines.append(f"votes: {tag.ok_votes}/{tag.total_votes}")

        lines.append("")
        lines.append("LAST DRIFT")
        lines.append("-------------------------")
        lines.append(f"Lap: {drift.lap}")
        lines.append(f"odom dx: {drift.odom_dx_m:+.3f} m")
        lines.append(f"odom dy: {drift.odom_dy_m:+.3f} m")
        lines.append(f"odom dyaw: {drift.odom_dyaw_deg:+.1f} deg")
        lines.append(f"tag err x: {drift.tag_err_x_px:+.1f}px")
        lines.append(f"tag err y: {drift.tag_err_y_px:+.1f}px")
        lines.append(f"tag err yaw: {drift.tag_err_yaw_deg:+.1f} deg")

        lines.append("")
        lines.append("ROUTE VARS")
        lines.append("-------------------------")
        lines.append(f"Recta derecha X: {rv['RECTA_DERECHA_X']:.3f}")
        lines.append(f"Recta izquierda X: {rv['RECTA_IZQUIERDA_X']:.3f}")
        lines.append(f"Recta superior Y: {rv['RECTA_SUPERIOR_Y']:.3f}")
        lines.append(f"Recta inferior Y: {rv['RECTA_INFERIOR_Y']:.3f}")

        lines.append("")
        lines.append("ANCHOR")
        lines.append("-------------------------")

        if anchor is None:
            lines.append("Anchor: not set")
        else:
            lines.append(f"Anchor x,y: {anchor.x:+.3f}, {anchor.y:+.3f}")
            lines.append(f"Forward yaw: {math.degrees(anchor.yaw):+.1f} deg")
            lines.append(f"Anchor gen: {snap['anchor_generation']}")

        lines.append("")
        lines.append("SEGMENT")
        lines.append("-------------------------")

        if dbg is None:
            lines.append("Debug: n/a")
        else:
            lines.append(f"From: {dbg.from_wp}")
            lines.append(f"To: {dbg.to_wp}")
            lines.append(f"Segment len: {dbg.seg_len:.3f} m")
            lines.append(f"Progress: {dbg.progress:.3f} m")
            lines.append(f"Remaining: {dbg.remaining:.3f} m")
            lines.append(f"Cross-track: {dbg.cross_track:+.3f} m")
            lines.append(f"Dist target: {dbg.dist_to_target:.3f} m")
            lines.append(f"Yaw err: {math.degrees(dbg.yaw_error):+.1f} deg")
            lines.append(f"cmd x: {dbg.linear_cmd:.3f}")
            lines.append(f"cmd y: {dbg.lateral_cmd:.3f}")
            lines.append(f"cmd w: {dbg.angular_cmd:.3f}")

        self.canvas.create_text(
            x0,
            y,
            anchor="nw",
            text="\n".join(lines),
            font=("Consolas", 10),
            fill="#111111",
        )

    def update(self):
        if self.closed:
            return

        self.canvas.delete("all")

        try:
            snap = self.node.get_gui_snapshot()

            self.draw_grid()

            self.draw_polyline(
                snap["black_route"],
                color="#333333",
                width=3,
                dash=(5, 4),
                closed=True,
            )

            self.draw_safety_band(snap["orange_route"])

            self.draw_polyline(
                snap["orange_route"],
                color="#ff9900",
                width=5,
                dash=None,
                closed=False,
            )

            self.draw_points(snap["orange_route"], color="#b36b00")
            self.draw_special_start(snap)
            self.draw_current_target(snap)
            self.draw_dog(snap)
            self.draw_text_panel(snap)

        except Exception as e:
            self.canvas.create_text(
                20,
                20,
                anchor="nw",
                text=f"Visualizer error: {e}",
                font=("Consolas", 12),
                fill="#ff0000",
            )

        self.root.after(self.update_ms, self.update)

    def run(self):
        self.root.mainloop()


# ============================================================
# MAIN
# ============================================================

def main(args=None):
    rclpy.init(args=args)

    node = SafeInsetPatrolNode()

    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    try:
        if node.enable_gui:
            visualizer = PatrolVisualizer(node)
            visualizer.run()
        else:
            while rclpy.ok():
                time.sleep(0.2)

    except KeyboardInterrupt:
        pass

    finally:
        try:
            node.publish_zero()
        except Exception:
            pass

        try:
            node.destroy_node()
        except Exception:
            pass

        try:
            if rclpy.ok():
                rclpy.shutdown()
        except Exception:
            pass

        try:
            spin_thread.join(timeout=1.0)
        except Exception:
            pass


if __name__ == "__main__":
    main()