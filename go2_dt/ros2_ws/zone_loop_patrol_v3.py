#!/usr/bin/env python3

import json
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any

import cv2
import numpy as np

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist


ANSI_RESET = "\033[0m"
ANSI_RED = "\033[91m"
ANSI_GREEN = "\033[92m"
ANSI_YELLOW = "\033[93m"
ANSI_BLUE = "\033[94m"
ANSI_MAGENTA = "\033[95m"
ANSI_CYAN = "\033[96m"
ANSI_WHITE = "\033[97m"


MMWAVE_PERSON_LABELS = {"person", "person_1", "human", "occupied", "true"}
MMWAVE_EMPTY_LABELS = {"empty", "nothing", "none", "clear", "background", "no_object", "no_novel_object", "false"}


def col(text: str, color: str) -> str:
    return f"{color}{text}{ANSI_RESET}"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_angle_deg(angle: float) -> float:
    while angle > 180.0:
        angle -= 360.0
    while angle < -180.0:
        angle += 360.0
    return angle


def normalize_angle_rad(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


def make_twist(linear_x: float = 0.0, linear_y: float = 0.0, angular_z: float = 0.0) -> Twist:
    msg = Twist()
    msg.linear.x = float(linear_x)
    msg.linear.y = float(linear_y)
    msg.angular.z = float(angular_z)
    return msg


def atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, path)


def read_json_file(path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[float]]:
    if not os.path.exists(path):
        return None, "missing_json", None

    try:
        mtime = os.path.getmtime(path)
    except Exception as exc:
        return None, f"mtime_error: {exc}", None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return None, f"read_error: {exc}", mtime

    if not isinstance(data, dict):
        return None, "json_is_not_object", mtime

    return data, None, mtime


@dataclass
class Action:
    action_type: str
    duration_sec: float
    linear_x: float
    linear_y: float
    angular_z: float

    def to_dict(self) -> Dict:
        return {
            "action_type": self.action_type,
            "duration_sec": float(self.duration_sec),
            "linear_x": float(self.linear_x),
            "linear_y": float(self.linear_y),
            "angular_z": float(self.angular_z),
        }

    @staticmethod
    def from_dict(data: Dict) -> "Action":
        return Action(
            action_type=str(data.get("action_type", "STOP")),
            duration_sec=float(data.get("duration_sec", 0.0)),
            linear_x=float(data.get("linear_x", 0.0)),
            linear_y=float(data.get("linear_y", 0.0)),
            angular_z=float(data.get("angular_z", 0.0)),
        )

    def command(self, scale: float = 1.0) -> Twist:
        return make_twist(
            linear_x=self.linear_x * scale,
            linear_y=self.linear_y * scale,
            angular_z=self.angular_z * scale,
        )


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

    cm_per_px: Optional[float] = None
    err_x_cm: Optional[float] = None
    err_y_cm: Optional[float] = None
    err_x_m: Optional[float] = None
    err_y_m: Optional[float] = None
    err_yaw_rad: Optional[float] = None

    instant_ok: bool = False
    stable_ok: bool = False
    ok_votes: int = 0
    total_votes: int = 0


@dataclass
class SensorReadState:
    enabled: bool
    online: bool = False
    stale: bool = True
    person_detected: bool = False
    age_sec: Optional[float] = None
    error: Optional[str] = None
    raw_status: Optional[str] = None
    prediction: Optional[str] = None
    raw_prediction: Optional[str] = None
    path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "online": bool(self.online),
            "stale": bool(self.stale),
            "person_detected": bool(self.person_detected),
            "age_sec": self.age_sec,
            "error": self.error,
            "raw_status": self.raw_status,
            "prediction": self.prediction,
            "raw_prediction": self.raw_prediction,
            "path": self.path,
        }


class AprilTagCamera:
    def __init__(
        self,
        camera_index: int,
        apriltag_id: int,
        tag_size_cm: float,
        tol_x_m: float,
        tol_y_m: float,
        tol_yaw_deg: float,
        vote_window: int,
        vote_required: int,
        show_window: bool,
        window_name: str = "AprilTag start calibrator",
    ):
        self.camera_index = int(camera_index)
        self.apriltag_id = int(apriltag_id)
        
        self.tag_size_cm = float(tag_size_cm)
        self.tol_x_m = float(tol_x_m)
        self.tol_y_m = float(tol_y_m)
        self.tol_yaw_deg = float(tol_yaw_deg)
        self.vote_window = int(vote_window)
        self.vote_required = int(vote_required)
        self.show_window = bool(show_window)
        self.window_name = window_name

        if not hasattr(self, "camera_device"):
            if "camera_device" in locals() and camera_device is not None:
                self.camera_device = str(camera_device)
            else:
                self.camera_device = f"/dev/video{int(self.camera_index)}"

        self.cap = cv2.VideoCapture(self.camera_device, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        if not self.cap.isOpened():
            raise RuntimeError(f"No se pudo abrir la cámara index={self.camera_index}")

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.cap.set(cv2.CAP_PROP_FPS, 30)

        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
        parameters = cv2.aruco.DetectorParameters()
        parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        parameters.cornerRefinementWinSize = 5
        parameters.cornerRefinementMaxIterations = 30
        parameters.cornerRefinementMinAccuracy = 0.01
        self.detector = cv2.aruco.ArucoDetector(dictionary, parameters)

        self.zero_set = False
        self.target_x = 0.0
        self.target_y = 0.0
        self.target_yaw_deg = 0.0

        self.ok_history = deque(maxlen=self.vote_window)
        self.last_state = AprilTagState()

    def close(self):
        try:
            self.cap.release()
        except Exception:
            pass
        try:
            cv2.destroyWindow(self.window_name)
        except Exception:
            pass

    def clear_votes(self):
        self.ok_history.clear()

    def draw_cross(self, frame, x, y, color, size=16, thickness=2):
        x = int(round(x))
        y = int(round(y))
        cv2.line(frame, (x - size, y), (x + size, y), color, thickness)
        cv2.line(frame, (x, y - size), (x, y + size), color, thickness)

    def compute_center(self, corners) -> Tuple[float, float]:
        pts = np.array(corners, dtype=np.float32)
        return float(np.mean(pts[:, 0])), float(np.mean(pts[:, 1]))

    def compute_yaw_deg(self, corners) -> float:
        pts = np.array(corners, dtype=np.float32)
        p0 = pts[0]
        p1 = pts[1]
        dx = float(p1[0] - p0[0])
        dy = float(p1[1] - p0[1])
        return normalize_angle_deg(math.degrees(math.atan2(dy, dx)))

    def compute_cm_per_px(self, corners) -> Optional[float]:
        pts = np.array(corners, dtype=np.float32)
        edge_lengths = []
        for i in range(4):
            p1 = pts[i]
            p2 = pts[(i + 1) % 4]
            edge_lengths.append(float(np.linalg.norm(p2 - p1)))
        avg_edge_px = float(np.mean(edge_lengths))
        if avg_edge_px <= 1.0:
            return None
        return self.tag_size_cm / avg_edge_px

    def set_zero_from_current_tag(self) -> bool:
        if not self.last_state.detected:
            return False
        self.target_x = float(self.last_state.cx)
        self.target_y = float(self.last_state.cy)
        self.target_yaw_deg = float(self.last_state.yaw_deg)
        self.zero_set = True
        self.ok_history.clear()
        return True

    def process_once(self) -> Tuple[AprilTagState, int]:
        ok, frame = self.cap.read()
        if not ok or frame is None:
            state = AprilTagState(detected=False, calibrated_zero=self.zero_set)
            self.last_state = state
            return state, -1

        h, w = frame.shape[:2]

        if self.zero_set:
            target_x = self.target_x
            target_y = self.target_y
            target_yaw = self.target_yaw_deg
        else:
            target_x = w / 2.0
            target_y = h / 2.0
            target_yaw = 0.0

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners_list, ids, _ = self.detector.detectMarkers(gray)

        detected = False
        selected_corners = None

        if ids is not None and len(ids) > 0:
            ids_flat = ids.flatten()
            for i, found_id in enumerate(ids_flat):
                if int(found_id) == self.apriltag_id:
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

        self.draw_cross(frame, target_x, target_y, (255, 0, 255), size=18, thickness=2)

        if detected and selected_corners is not None:
            cx, cy = self.compute_center(selected_corners)
            yaw_deg = self.compute_yaw_deg(selected_corners)
            cm_per_px = self.compute_cm_per_px(selected_corners)

            err_x_px = cx - target_x
            err_y_px = cy - target_y
            err_yaw_deg = normalize_angle_deg(yaw_deg - target_yaw)

            err_x_cm = None
            err_y_cm = None
            err_x_m = None
            err_y_m = None

            if cm_per_px is not None:
                err_x_cm = err_x_px * cm_per_px
                err_y_cm = err_y_px * cm_per_px
                err_x_m = err_x_cm / 100.0
                err_y_m = err_y_cm / 100.0

            err_yaw_rad = math.radians(err_yaw_deg)

            instant_ok = (
                self.zero_set
                and err_x_m is not None
                and err_y_m is not None
                and abs(err_x_m) <= self.tol_x_m
                and abs(err_y_m) <= self.tol_y_m
                and abs(err_yaw_deg) <= self.tol_yaw_deg
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
                err_x_px=err_x_px,
                err_y_px=err_y_px,
                err_yaw_deg=err_yaw_deg,
                cm_per_px=cm_per_px,
                err_x_cm=err_x_cm,
                err_y_cm=err_y_cm,
                err_x_m=err_x_m,
                err_y_m=err_y_m,
                err_yaw_rad=err_yaw_rad,
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
                title = "PRESS C TO SET START"
            elif stable_ok:
                title = "START POSE OK"
            else:
                title = "DIRECT ALIGN: image position -> cmd_vel"

            cv2.putText(frame, title, (30, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 3)
            cv2.putText(
                frame,
                f"px: err_x={err_x_px:+.1f} err_y={err_y_px:+.1f} yaw={err_yaw_deg:+.2f}deg",
                (30, 80),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
            )

            if err_x_m is not None and err_y_m is not None:
                cv2.putText(
                    frame,
                    f"m: x={err_x_m:+.3f} y={err_y_m:+.3f} scale={cm_per_px:.4f}cm/px",
                    (30, 115),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (255, 255, 255),
                    2,
                )

            cv2.putText(
                frame,
                f"votes={ok_votes}/{total_votes} required={self.vote_required}/{self.vote_window}",
                (30, 150),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
            )

            cv2.putText(
                frame,
                "mapping: left->forward | right->back | above->right | below->left",
                (30, 185),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (255, 255, 255),
                2,
            )
        else:
            if self.zero_set:
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

        key = -1
        if self.show_window:
            cv2.imshow(self.window_name, frame)
            key = cv2.waitKey(1) & 0xFF

        self.last_state = state
        return state, key


class PhaseTeachReplayPatrol(Node):
    def __init__(self):
        super().__init__("zone_loop_patrol_v4")

        self.lock = threading.RLock()

        self.declare_parameter("mode", "teach")
        self.declare_parameter("cmd_topic", "/cmd_vel_out")

        self.declare_parameter("output_file", "/home/nextnet/AlbertoDir/go2_dt/ros2_ws/phase_trained_lap.json")
        self.declare_parameter("input_file", "/home/nextnet/AlbertoDir/go2_dt/ros2_ws/phase_trained_lap.json")

        self.declare_parameter("loop_hz", 25.0)

        self.declare_parameter("forward_speed", 0.060)
        self.declare_parameter("backward_speed", 0.045)
        self.declare_parameter("strafe_speed", 0.035)
        self.declare_parameter("turn_speed", 0.16)

        self.declare_parameter("max_action_duration_sec", 120.0)

        self.declare_parameter("repeat_auto", False)
        self.declare_parameter("replay_speed_scale", 1.0)
        self.declare_parameter("between_actions_stop_sec", 0.25)

        self.declare_parameter("camera_index", 0)
        self.declare_parameter("apriltag_id", 0)
        self.declare_parameter("apriltag_camera_device", "/dev/video0")
        self.declare_parameter("show_camera_window", True)

        self.declare_parameter("tag_size_cm", 8.0)
        self.declare_parameter("tag_tol_x_m", 0.05)
        self.declare_parameter("tag_tol_y_m", 0.05)
        self.declare_parameter("tag_tol_yaw_deg", 2.0)
        self.declare_parameter("tag_vote_window", 10)
        self.declare_parameter("tag_vote_required", 8)

        self.declare_parameter("auto_align_enabled", True)

        self.declare_parameter("camera_align_initial_stop_sec", 1.0)
        self.declare_parameter("camera_align_hard_stop_sec", 0.8)

        # Nuevo modo: control determinista cámara -> cmd_vel.
        self.declare_parameter("direct_align_enabled", True)
        self.declare_parameter("position_first", True)
        self.declare_parameter("camera_align_position_gain", 0.45)
        self.declare_parameter("camera_align_min_xy_move_sec", 0.10)
        self.declare_parameter("camera_align_max_xy_move_sec", 0.45)
        self.declare_parameter("camera_align_min_yaw_move_sec", 0.10)
        self.declare_parameter("camera_align_max_yaw_move_sec", 0.35)

        self.declare_parameter("camera_align_linear_x_speed_m_s", 0.055)
        self.declare_parameter("camera_align_linear_y_speed_m_s", 0.045)
        self.declare_parameter("camera_align_angular_speed_rad_s", 0.12)

        # Signos para adaptar la convención real del Go2 si hiciera falta.
        # Por defecto se aplica el mapa pedido:
        #   tag izquierda -> adelante
        #   tag derecha   -> atrás
        #   tag arriba    -> derecha
        #   tag abajo     -> izquierda
        # Si un eje sale invertido, cambia solo cmd_x_sign, cmd_y_sign o cmd_w_sign desde el launcher.
        self.declare_parameter("cmd_x_sign", 1.0)
        self.declare_parameter("cmd_y_sign", 1.0)
        self.declare_parameter("cmd_w_sign", -1.0)

        # Compatibilidad con versiones anteriores. Solo se usa si direct_align_enabled=false.
        self.declare_parameter("camera_align_move_gain", 0.70)
        self.declare_parameter("camera_align_min_move_sec", 0.35)
        self.declare_parameter("camera_align_max_move_sec", 2.5)
        self.declare_parameter("camera_max_align_attempts", 50)
        self.declare_parameter("camera_axis_order", "Y,X,YAW")
        self.declare_parameter("yaw_only_when_xy_within_m", 0.10)

        self.declare_parameter(
            "patrol_state_path",
            "/home/nextnet/AlbertoDir/go2_dt/ros2_ws/patrol_outputs/live_patrol_state.json",
        )
        self.declare_parameter(
            "camera_state_path",
            "/home/nextnet/AlbertoDir/go2_dt/camera_yolo/outputs/live_camera_state.json",
        )
        self.declare_parameter(
            "mmwave_state_path",
            "/home/nextnet/AlbertoDir/go2_dt/csi_dog_dataset_20210421_181125/analysis_outputs/live_prediction_state.json",
        )
        self.declare_parameter(
            "lidar_state_path",
            "/home/nextnet/AlbertoDir/go2_dt/ros2_ws/lidar_outputs/live_lidar_state.json",
        )

        self.declare_parameter("enable_camera_stop", True)
        self.declare_parameter("enable_mmwave_stop", True)
        self.declare_parameter("enable_lidar_stop", False)

        self.declare_parameter("sensor_state_max_age_sec", 3.0)
        self.declare_parameter("person_confirm_sec", 2.0)
        self.declare_parameter("clear_confirm_sec", 2.0)
        self.declare_parameter("patrol_state_write_period_sec", 0.10)
        self.declare_parameter("safety_stop_during_alignment", False)

        self.mode = str(self.get_parameter("mode").value).lower().strip()
        self.cmd_topic = str(self.get_parameter("cmd_topic").value)

        self.output_file = str(self.get_parameter("output_file").value)
        self.input_file = str(self.get_parameter("input_file").value)

        self.loop_hz = float(self.get_parameter("loop_hz").value)

        self.forward_speed = float(self.get_parameter("forward_speed").value)
        self.backward_speed = float(self.get_parameter("backward_speed").value)
        self.strafe_speed = float(self.get_parameter("strafe_speed").value)
        self.turn_speed = float(self.get_parameter("turn_speed").value)

        self.max_action_duration_sec = float(self.get_parameter("max_action_duration_sec").value)

        self.repeat_auto = bool(self.get_parameter("repeat_auto").value)
        self.replay_speed_scale = float(self.get_parameter("replay_speed_scale").value)
        self.between_actions_stop_sec = float(self.get_parameter("between_actions_stop_sec").value)

        camera_index = int(self.get_parameter("camera_index").value)
        apriltag_id = int(self.get_parameter("apriltag_id").value)
        apriltag_camera_device = str(self.get_parameter("apriltag_camera_device").value)
        show_camera_window = bool(self.get_parameter("show_camera_window").value)

        tag_size_cm = float(self.get_parameter("tag_size_cm").value)
        tag_tol_x_m = float(self.get_parameter("tag_tol_x_m").value)
        tag_tol_y_m = float(self.get_parameter("tag_tol_y_m").value)
        tag_tol_yaw_deg = float(self.get_parameter("tag_tol_yaw_deg").value)
        tag_vote_window = int(self.get_parameter("tag_vote_window").value)
        tag_vote_required = int(self.get_parameter("tag_vote_required").value)

        self.auto_align_enabled = bool(self.get_parameter("auto_align_enabled").value)

        self.camera_align_initial_stop_sec = float(self.get_parameter("camera_align_initial_stop_sec").value)
        self.camera_align_hard_stop_sec = float(self.get_parameter("camera_align_hard_stop_sec").value)

        self.direct_align_enabled = bool(self.get_parameter("direct_align_enabled").value)
        self.position_first = bool(self.get_parameter("position_first").value)
        self.camera_align_position_gain = float(self.get_parameter("camera_align_position_gain").value)
        self.camera_align_min_xy_move_sec = float(self.get_parameter("camera_align_min_xy_move_sec").value)
        self.camera_align_max_xy_move_sec = float(self.get_parameter("camera_align_max_xy_move_sec").value)
        self.camera_align_min_yaw_move_sec = float(self.get_parameter("camera_align_min_yaw_move_sec").value)
        self.camera_align_max_yaw_move_sec = float(self.get_parameter("camera_align_max_yaw_move_sec").value)

        self.camera_align_linear_x_speed_m_s = float(self.get_parameter("camera_align_linear_x_speed_m_s").value)
        self.camera_align_linear_y_speed_m_s = float(self.get_parameter("camera_align_linear_y_speed_m_s").value)
        self.camera_align_angular_speed_rad_s = float(self.get_parameter("camera_align_angular_speed_rad_s").value)

        self.cmd_x_sign = float(self.get_parameter("cmd_x_sign").value)
        self.cmd_y_sign = float(self.get_parameter("cmd_y_sign").value)
        self.cmd_w_sign = float(self.get_parameter("cmd_w_sign").value)

        self.camera_align_move_gain = float(self.get_parameter("camera_align_move_gain").value)
        self.camera_align_min_move_sec = float(self.get_parameter("camera_align_min_move_sec").value)
        self.camera_align_max_move_sec = float(self.get_parameter("camera_align_max_move_sec").value)

        self.camera_max_align_attempts = int(self.get_parameter("camera_max_align_attempts").value)

        axis_order_raw = str(self.get_parameter("camera_axis_order").value)
        self.camera_axis_order = [x.strip().upper() for x in axis_order_raw.split(",") if x.strip()]
        self.yaw_only_when_xy_within_m = float(self.get_parameter("yaw_only_when_xy_within_m").value)

        self.patrol_state_path = str(self.get_parameter("patrol_state_path").value)
        self.camera_state_path = str(self.get_parameter("camera_state_path").value)
        self.mmwave_state_path = str(self.get_parameter("mmwave_state_path").value)
        self.lidar_state_path = str(self.get_parameter("lidar_state_path").value)

        self.enable_camera_stop = bool(self.get_parameter("enable_camera_stop").value)
        self.enable_mmwave_stop = bool(self.get_parameter("enable_mmwave_stop").value)
        self.enable_lidar_stop = bool(self.get_parameter("enable_lidar_stop").value)

        self.sensor_state_max_age_sec = float(self.get_parameter("sensor_state_max_age_sec").value)
        self.person_confirm_sec = float(self.get_parameter("person_confirm_sec").value)
        self.clear_confirm_sec = float(self.get_parameter("clear_confirm_sec").value)
        self.patrol_state_write_period_sec = float(self.get_parameter("patrol_state_write_period_sec").value)
        self.safety_stop_during_alignment = bool(self.get_parameter("safety_stop_during_alignment").value)

        self.tag_camera = AprilTagCamera(
            camera_index=camera_index,
            apriltag_id=apriltag_id,
            tag_size_cm=tag_size_cm,
            tol_x_m=tag_tol_x_m,
            tol_y_m=tag_tol_y_m,
            tol_yaw_deg=tag_tol_yaw_deg,
            vote_window=tag_vote_window,
            vote_required=tag_vote_required,
            show_window=show_camera_window,
        )

        self.cmd_pub = self.create_publisher(Twist, self.cmd_topic, 10)
        self.timer = self.create_timer(1.0 / self.loop_hz, self.on_timer)

        self.state = "WAIT_ZERO"
        self.state_enter_time = time.monotonic()

        self.last_tag = AprilTagState()

        self.actions: List[Action] = []
        self.current_action_type = "STOP"
        self.current_cmd = make_twist()
        self.current_action_start_time: Optional[float] = None

        self.loaded_actions: List[Action] = []
        self.current_action_index = 0

        self.current_action_start_time_auto: Optional[float] = None
        self.action_accumulated_elapsed_sec = 0.0
        self.action_last_resume_time_auto: Optional[float] = None

        self.completed_laps = 0

        self.camera_move_cmd = make_twist()
        self.camera_move_end_time = 0.0
        self.camera_align_attempts = 0
        self.last_camera_axis = "NONE"
        self.last_direct_rule = "NONE"
        self.last_direct_error_x_m = 0.0
        self.last_direct_error_y_m = 0.0

        now = time.monotonic()
        self.person_since: Optional[float] = None
        self.clear_since: Optional[float] = now
        self.person_detected_now = False
        self.person_detected_stable = False
        self.clear_stable = False
        self.person_stable_sec = 0.0
        self.clear_stable_sec = 0.0
        self.triggered_sources: List[str] = []
        self.safety_previous_state: Optional[str] = None
        self.safety_stop_reason: Optional[str] = None

        self.sensor_states: Dict[str, SensorReadState] = {
            "camera": SensorReadState(enabled=self.enable_camera_stop, path=self.camera_state_path),
            "mmwave": SensorReadState(enabled=self.enable_mmwave_stop, path=self.mmwave_state_path),
            "lidar": SensorReadState(enabled=self.enable_lidar_stop, path=self.lidar_state_path),
        }

        self.start_pose_event_id = 0
        self.last_start_pose_event_timestamp: Optional[float] = None
        self.last_start_pose_event_lap_index = 0
        self.last_patrol_state_write_mono = 0.0

        if self.mode == "auto":
            self.load_recording()

        self.log_event(
            "INIT",
            f"mode={self.mode} | cmd_topic={self.cmd_topic} | "
            f"input_file={self.input_file} | output_file={self.output_file}",
            ANSI_GREEN,
            warn=True,
        )

        self.log_event(
            "SAFETY_JSON",
            f"patrol_state={self.patrol_state_path} | camera={self.camera_state_path} | "
            f"mmwave={self.mmwave_state_path} | lidar={self.lidar_state_path} | "
            f"confirm={self.person_confirm_sec:.1f}s | clear={self.clear_confirm_sec:.1f}s",
            ANSI_CYAN,
            warn=True,
        )

        self.log_event(
            "DIRECT_CAMERA_ALIGN",
            f"enabled={self.direct_align_enabled} | position_first={self.position_first} | "
            f"vx={self.camera_align_linear_x_speed_m_s:.3f}m/s | "
            f"vy={self.camera_align_linear_y_speed_m_s:.3f}m/s | "
            f"w={self.camera_align_angular_speed_rad_s:.3f}rad/s | "
            f"gain={self.camera_align_position_gain:.2f} | hard_stop={self.camera_align_hard_stop_sec:.1f}s | "
            f"signs=({self.cmd_x_sign:+.1f},{self.cmd_y_sign:+.1f},{self.cmd_w_sign:+.1f})",
            ANSI_MAGENTA,
            warn=True,
        )

        if self.mode == "teach":
            self.log_event(
                "TEACH",
                "Coloca el perro en inicio. Pulsa C. Luego W/A/D/S/Q/E/X para fases y G para guardar.",
                ANSI_YELLOW,
                warn=True,
            )
        elif self.mode == "auto":
            self.log_event(
                "AUTO",
                "Coloca el perro en inicio. Pulsa C. Reproduce vuelta, se para por cámara/mmWave/LiDAR y ajusta por AprilTag al final.",
                ANSI_YELLOW,
                warn=True,
            )
        else:
            raise RuntimeError("mode debe ser 'teach' o 'auto'")

        self.write_patrol_state(force=True)

    # ========================================================
    # Logging / state helpers
    # ========================================================

    def log_event(self, tag: str, msg: str, color: str = ANSI_WHITE, warn: bool = False):
        text = col(f"[{tag}] {msg}", color)
        if warn:
            self.get_logger().warn(text)
        else:
            self.get_logger().info(text)

    def set_state(self, new_state: str):
        old = self.state
        self.state = new_state
        self.state_enter_time = time.monotonic()
        self.log_event("STATE", f"{old} -> {new_state}", ANSI_CYAN)

    def state_elapsed(self) -> float:
        return time.monotonic() - self.state_enter_time

    def publish_zero(self):
        try:
            self.cmd_pub.publish(make_twist())
        except Exception:
            pass

    def fmt_m(self, value: Optional[float]) -> str:
        if value is None:
            return "NA"
        return f"{value:+.3f}m"

    # ========================================================
    # Sensor JSON reading + temporal stability
    # ========================================================

    def _age_from_mtime(self, mtime: Optional[float]) -> Optional[float]:
        if mtime is None:
            return None
        return max(0.0, time.time() - float(mtime))

    def read_camera_sensor_state(self) -> SensorReadState:
        state = SensorReadState(enabled=self.enable_camera_stop, path=self.camera_state_path)
        if not state.enabled:
            return state

        data, error, mtime = read_json_file(self.camera_state_path)
        age = self._age_from_mtime(mtime)
        state.age_sec = age
        state.stale = True if age is None else age > self.sensor_state_max_age_sec

        if error is not None:
            state.error = error
            return state

        raw_status = str(data.get("status", "online")).strip().lower()
        state.raw_status = raw_status
        state.online = raw_status not in {"fault", "offline", "stale", "error"} and not state.stale
        state.person_detected = bool(data.get("person_detected", False)) if state.online else False
        return state

    def read_lidar_sensor_state(self) -> SensorReadState:
        state = SensorReadState(enabled=self.enable_lidar_stop, path=self.lidar_state_path)
        if not state.enabled:
            return state

        data, error, mtime = read_json_file(self.lidar_state_path)
        age = self._age_from_mtime(mtime)
        state.age_sec = age
        state.stale = True if age is None else age > self.sensor_state_max_age_sec

        if error is not None:
            state.error = error
            return state

        raw_status = str(data.get("status", "online")).strip().lower()
        state.raw_status = raw_status
        state.online = raw_status not in {"fault", "offline", "stale", "error"} and not state.stale

        if not state.online:
            state.person_detected = False
            return state

        if "person_detected" in data:
            state.person_detected = bool(data.get("person_detected", False))
        elif "occupied" in data:
            state.person_detected = bool(data.get("occupied", False))
        else:
            try:
                state.person_detected = int(data.get("count", 0)) > 0
            except Exception:
                state.person_detected = False

        state.prediction = "person" if state.person_detected else "empty"
        state.raw_prediction = state.prediction
        return state

    def read_mmwave_sensor_state(self) -> SensorReadState:
        state = SensorReadState(enabled=self.enable_mmwave_stop, path=self.mmwave_state_path)
        if not state.enabled:
            return state

        data, error, mtime = read_json_file(self.mmwave_state_path)
        age = self._age_from_mtime(mtime)
        state.age_sec = age
        state.stale = True if age is None else age > self.sensor_state_max_age_sec

        if error is not None:
            state.error = error
            return state

        prediction = str(data.get("prediction", "")).strip().lower()
        raw_prediction = str(data.get("raw_prediction", "")).strip().lower()
        label = str(data.get("label", "")).strip().lower()
        candidates = [prediction, raw_prediction, label]

        state.prediction = prediction or None
        state.raw_prediction = raw_prediction or None
        state.online = not state.stale

        person = False
        for value in candidates:
            if value in MMWAVE_PERSON_LABELS:
                person = True
                break
            if value in MMWAVE_EMPTY_LABELS:
                person = False
                break

        if not person:
            try:
                if int(data.get("count", 0)) > 0:
                    person = True
            except Exception:
                pass

        state.person_detected = bool(person) if state.online else False
        return state

    def update_sensor_states(self):
        self.sensor_states["camera"] = self.read_camera_sensor_state()
        self.sensor_states["mmwave"] = self.read_mmwave_sensor_state()
        self.sensor_states["lidar"] = self.read_lidar_sensor_state()

        triggered = []
        for name, s in self.sensor_states.items():
            if s.enabled and s.online and not s.stale and s.person_detected:
                triggered.append(name)

        now = time.monotonic()
        self.person_detected_now = bool(triggered)
        self.triggered_sources = triggered

        if self.person_detected_now:
            if self.person_since is None:
                self.person_since = now
            self.clear_since = None
        else:
            if self.clear_since is None:
                self.clear_since = now
            self.person_since = None

        self.person_stable_sec = 0.0 if self.person_since is None else max(0.0, now - self.person_since)
        self.clear_stable_sec = 0.0 if self.clear_since is None else max(0.0, now - self.clear_since)

        self.person_detected_stable = self.person_since is not None and self.person_stable_sec >= self.person_confirm_sec
        self.clear_stable = self.clear_since is not None and self.clear_stable_sec >= self.clear_confirm_sec

    def state_allows_safety_stop(self) -> bool:
        if self.mode != "auto":
            return False
        if self.state in {"WAIT_ZERO", "AUTO_DONE", "TEACH_RECORDING", "TEACH_DONE", "SAFETY_STOP_PERSON"}:
            return False
        if self.state in {"AUTO_ACTION", "AUTO_BETWEEN_ACTIONS"}:
            return True
        if self.safety_stop_during_alignment and self.state in {
            "POST_LAP_STOP",
            "CAMERA_EVALUATE",
            "CAMERA_METRIC_MOVE",
            "CAMERA_AFTER_METRIC_STOP",
        }:
            return True
        return False

    def maybe_enter_safety_stop(self) -> bool:
        if not self.state_allows_safety_stop():
            return False
        if not self.person_detected_stable:
            return False

        self.publish_zero()
        self.safety_previous_state = self.state
        self.safety_stop_reason = "person_detected_by_" + "+".join(self.triggered_sources or ["unknown"])

        if self.state == "AUTO_ACTION":
            self.pause_current_auto_action()

        self.log_event(
            "SAFETY_STOP",
            f"Parada por persona estable ({self.person_stable_sec:.2f}s) | sources={self.triggered_sources}",
            ANSI_RED,
            warn=True,
        )
        self.set_state("SAFETY_STOP_PERSON")
        return True

    def handle_safety_stop(self) -> bool:
        if self.state != "SAFETY_STOP_PERSON":
            return False

        self.publish_zero()

        if not self.clear_stable:
            return True

        previous = self.safety_previous_state or "AUTO_ACTION"
        self.log_event(
            "SAFETY_RESUME",
            f"Zona limpia estable ({self.clear_stable_sec:.2f}s). Reanudando estado={previous}",
            ANSI_GREEN,
            warn=True,
        )

        if previous == "AUTO_ACTION":
            self.resume_current_auto_action()

        self.safety_previous_state = None
        self.safety_stop_reason = None
        self.set_state(previous)
        return True

    # ========================================================
    # Auto action pause/resume helpers
    # ========================================================

    def reset_current_auto_action_timer(self):
        now = time.monotonic()
        self.current_action_start_time_auto = now
        self.action_accumulated_elapsed_sec = 0.0
        self.action_last_resume_time_auto = now

    def current_auto_action_elapsed(self) -> float:
        elapsed = float(self.action_accumulated_elapsed_sec)
        if self.action_last_resume_time_auto is not None:
            elapsed += time.monotonic() - self.action_last_resume_time_auto
        return max(0.0, elapsed)

    def pause_current_auto_action(self):
        if self.action_last_resume_time_auto is not None:
            self.action_accumulated_elapsed_sec += time.monotonic() - self.action_last_resume_time_auto
            self.action_last_resume_time_auto = None

    def resume_current_auto_action(self):
        if self.action_last_resume_time_auto is None:
            self.action_last_resume_time_auto = time.monotonic()

    # ========================================================
    # Recording / loading
    # ========================================================

    def save_recording(self):
        if self.current_action_start_time is not None:
            self.save_current_action()

        if not self.actions:
            self.log_event("SAVE", "No hay acciones grabadas. No guardo nada.", ANSI_RED, warn=True)
            return

        payload = {
            "version": 6,
            "type": "zone_loop_patrol_direct_camera_align",
            "created_at": time.time(),
            "cmd_topic": self.cmd_topic,
            "forward_speed": self.forward_speed,
            "backward_speed": self.backward_speed,
            "strafe_speed": self.strafe_speed,
            "turn_speed": self.turn_speed,
            "apriltag_zero": {
                "x_px": self.tag_camera.target_x,
                "y_px": self.tag_camera.target_y,
                "yaw_deg": self.tag_camera.target_yaw_deg,
            },
            "action_count": len(self.actions),
            "total_duration_sec": sum(a.duration_sec for a in self.actions),
            "actions": [a.to_dict() for a in self.actions],
        }

        os.makedirs(os.path.dirname(self.output_file), exist_ok=True)
        tmp = self.output_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self.output_file)

        self.log_event(
            "SAVE",
            f"Grabación guardada en {self.output_file} | "
            f"actions={len(self.actions)} | duration={payload['total_duration_sec']:.2f}s",
            ANSI_GREEN,
            warn=True,
        )

        for i, action in enumerate(self.actions):
            self.log_event(
                "ACTION",
                f"{i:03d} {action.action_type:<12} "
                f"duration={action.duration_sec:.2f}s "
                f"cmd_x={action.linear_x:+.3f} "
                f"cmd_y={action.linear_y:+.3f} "
                f"cmd_w={action.angular_z:+.3f}",
                ANSI_WHITE,
                warn=True,
            )

    def load_recording(self):
        if not os.path.exists(self.input_file):
            raise RuntimeError(f"No existe input_file: {self.input_file}")

        with open(self.input_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.loaded_actions = [Action.from_dict(x) for x in data.get("actions", [])]

        if not self.loaded_actions:
            raise RuntimeError("La grabación no contiene acciones.")

        self.log_event(
            "AUTO",
            f"Recording loaded | actions={len(self.loaded_actions)} | "
            f"duration={sum(a.duration_sec for a in self.loaded_actions):.2f}s",
            ANSI_GREEN,
            warn=True,
        )

    def action_type_to_cmd(self, action_type: str) -> Twist:
        if action_type == "FORWARD":
            return make_twist(linear_x=self.forward_speed)
        if action_type == "BACKWARD":
            return make_twist(linear_x=-self.backward_speed)
        if action_type == "TURN_LEFT":
            return make_twist(angular_z=self.turn_speed)
        if action_type == "TURN_RIGHT":
            return make_twist(angular_z=-self.turn_speed)
        if action_type == "STRAFE_LEFT":
            return make_twist(linear_y=self.strafe_speed)
        if action_type == "STRAFE_RIGHT":
            return make_twist(linear_y=-self.strafe_speed)
        return make_twist()

    def save_current_action(self):
        if self.current_action_start_time is None:
            return

        now = time.monotonic()
        duration = now - self.current_action_start_time

        if duration <= 0.05:
            return

        if duration > self.max_action_duration_sec:
            self.log_event(
                "SAFETY",
                f"Acción {self.current_action_type} demasiado larga ({duration:.2f}s). "
                f"La recorto a {self.max_action_duration_sec:.2f}s.",
                ANSI_RED,
                warn=True,
            )
            duration = self.max_action_duration_sec

        action = Action(
            action_type=self.current_action_type,
            duration_sec=duration,
            linear_x=float(self.current_cmd.linear.x),
            linear_y=float(self.current_cmd.linear.y),
            angular_z=float(self.current_cmd.angular.z),
        )

        self.actions.append(action)

        self.log_event(
            "ACTION_SAVED",
            f"{len(self.actions)-1:03d} {action.action_type:<12} "
            f"duration={action.duration_sec:.2f}s "
            f"cmd_x={action.linear_x:+.3f} "
            f"cmd_y={action.linear_y:+.3f} "
            f"cmd_w={action.angular_z:+.3f}",
            ANSI_MAGENTA,
            warn=True,
        )

    def start_new_action(self, action_type: str):
        if self.state != "TEACH_RECORDING":
            return

        self.save_current_action()
        self.current_action_type = action_type
        self.current_cmd = self.action_type_to_cmd(action_type)
        self.current_action_start_time = time.monotonic()
        self.log_event("ACTION_START", action_type, ANSI_YELLOW, warn=True)

    def handle_teach_key(self, key: int):
        if self.state != "TEACH_RECORDING":
            return

        if key in (ord("w"), ord("W")):
            self.start_new_action("FORWARD")
        elif key in (ord("s"), ord("S")):
            self.start_new_action("BACKWARD")
        elif key in (ord("a"), ord("A")):
            self.start_new_action("TURN_LEFT")
        elif key in (ord("d"), ord("D")):
            self.start_new_action("TURN_RIGHT")
        elif key in (ord("q"), ord("Q")):
            self.start_new_action("STRAFE_LEFT")
        elif key in (ord("e"), ord("E")):
            self.start_new_action("STRAFE_RIGHT")
        elif key in (ord("x"), ord("X")):
            self.start_new_action("STOP")
        elif key in (ord("g"), ord("G")):
            self.publish_zero()
            self.save_recording()
            self.set_state("TEACH_DONE")

    # ========================================================
    # Auto replay
    # ========================================================

    def start_replay(self):
        self.current_action_index = 0
        self.reset_current_auto_action_timer()
        self.set_state("AUTO_ACTION")

        self.log_event(
            "AUTO",
            f"Replay started | lap={self.completed_laps + 1} | actions={len(self.loaded_actions)}",
            ANSI_GREEN,
            warn=True,
        )

    def effective_action_duration(self, action: Action) -> float:
        scale = max(0.05, abs(self.replay_speed_scale))
        return action.duration_sec / scale

    def effective_command(self, action: Action) -> Twist:
        scale = max(0.05, abs(self.replay_speed_scale))
        return action.command(scale=scale)

    def handle_auto_action(self):
        if self.state != "AUTO_ACTION":
            return False

        if self.current_action_index >= len(self.loaded_actions):
            self.finish_lap()
            return True

        action = self.loaded_actions[self.current_action_index]
        elapsed = self.current_auto_action_elapsed()
        target_duration = self.effective_action_duration(action)

        if elapsed >= target_duration:
            self.publish_zero()

            self.log_event(
                "ACTION_DONE",
                f"{self.current_action_index:03d}/{len(self.loaded_actions):03d} "
                f"{action.action_type:<12} elapsed={elapsed:.2f}s target={target_duration:.2f}s",
                ANSI_BLUE,
            )

            self.current_action_index += 1

            if self.current_action_index >= len(self.loaded_actions):
                self.finish_lap()
                return True

            self.action_accumulated_elapsed_sec = 0.0
            self.action_last_resume_time_auto = None
            self.current_action_start_time_auto = None
            self.set_state("AUTO_BETWEEN_ACTIONS")
            return True

        cmd = self.effective_command(action)
        self.cmd_pub.publish(cmd)

        self.log_event(
            "REPLAY",
            f"idx={self.current_action_index:03d}/{len(self.loaded_actions):03d} "
            f"{action.action_type:<12} t={elapsed:.2f}/{target_duration:.2f}s "
            f"cmd_x={cmd.linear.x:+.3f} cmd_y={cmd.linear.y:+.3f} cmd_w={cmd.angular.z:+.3f}",
            ANSI_WHITE,
        )

        return True

    def handle_between_actions(self):
        if self.state != "AUTO_BETWEEN_ACTIONS":
            return False

        self.publish_zero()

        if self.state_elapsed() >= self.between_actions_stop_sec:
            self.reset_current_auto_action_timer()
            self.set_state("AUTO_ACTION")

        return True

    def finish_lap(self):
        self.publish_zero()
        self.completed_laps += 1

        self.log_event(
            "AUTO",
            f"Vuelta terminada | completed_laps={self.completed_laps}",
            ANSI_GREEN,
            warn=True,
        )

        if not self.auto_align_enabled:
            self.publish_start_pose_event(self.last_tag)
            if self.repeat_auto:
                self.start_replay()
            else:
                self.set_state("AUTO_DONE")
            return

        self.camera_align_attempts = 0
        self.tag_camera.clear_votes()
        self.last_camera_axis = "NONE"
        self.last_direct_rule = "NONE"
        self.camera_move_cmd = make_twist()
        self.camera_move_end_time = 0.0
        self.set_state("POST_LAP_STOP")

    # ========================================================
    # Direct AprilTag alignment
    # ========================================================

    def xy_error_norm_m(self, tag: AprilTagState) -> Optional[float]:
        if tag.err_x_m is None or tag.err_y_m is None:
            return None
        return math.hypot(tag.err_x_m, tag.err_y_m)

    def position_needs_correction(self, tag: AprilTagState) -> bool:
        if tag.err_x_m is None or tag.err_y_m is None:
            return False
        return abs(tag.err_x_m) > self.tag_camera.tol_x_m or abs(tag.err_y_m) > self.tag_camera.tol_y_m

    def yaw_needs_correction(self, tag: AprilTagState) -> bool:
        return abs(tag.err_yaw_deg) > self.tag_camera.tol_yaw_deg

    def compute_direct_camera_frame_move(self, tag: AprilTagState) -> Tuple[str, Twist, float]:
        """
        Control determinista en frame de cámara, sin ensayo-error.

        Reglas validadas:
        - Tag a la izquierda de la cruz  -> robot hacia delante.
        - Tag a la derecha de la cruz    -> robot hacia atrás.
        - Tag por encima de la cruz      -> robot hacia la derecha.
        - Tag por debajo de la cruz      -> robot hacia la izquierda.

        Convención ROS/Go2 validada en tu prueba:
        linear.x > 0  -> adelante
        linear.x < 0  -> atrás
        linear.y > 0  -> izquierda
        linear.y < 0  -> derecha
        """
        if tag.err_x_m is None or tag.err_y_m is None:
            return "NONE", make_twist(), 0.0

        err_x = float(tag.err_x_m)
        err_y = float(tag.err_y_m)

        abs_x = abs(err_x)
        abs_y = abs(err_y)

        vx = abs(self.camera_align_linear_x_speed_m_s)
        vy = abs(self.camera_align_linear_y_speed_m_s)

        self.last_direct_error_x_m = err_x
        self.last_direct_error_y_m = err_y

        norm_x = abs_x / max(self.tag_camera.tol_x_m, 1e-6)
        norm_y = abs_y / max(self.tag_camera.tol_y_m, 1e-6)

        # Corrige primero el eje más desviado respecto a su tolerancia.
        if norm_x >= norm_y:
            if abs_x <= self.tag_camera.tol_x_m or vx <= 1e-6:
                return "NONE", make_twist(), 0.0

            if err_x > 0.0:
                # Tag a la derecha de la cruz -> robot hacia atrás.
                cmd = make_twist(linear_x=-self.cmd_x_sign * vx)
                rule = "TAG_RIGHT_GO_BACKWARD"
            else:
                # Tag a la izquierda de la cruz -> robot hacia delante.
                cmd = make_twist(linear_x=self.cmd_x_sign * vx)
                rule = "TAG_LEFT_GO_FORWARD"

            duration = abs_x / vx * self.camera_align_position_gain
            duration = clamp(duration, self.camera_align_min_xy_move_sec, self.camera_align_max_xy_move_sec)

            self.last_direct_rule = rule
            return rule, cmd, duration

        if abs_y <= self.tag_camera.tol_y_m or vy <= 1e-6:
            return "NONE", make_twist(), 0.0

        if err_y > 0.0:
            # Tag por debajo de la cruz -> robot hacia la izquierda.
            cmd = make_twist(linear_y=self.cmd_y_sign * vy)
            rule = "TAG_BELOW_GO_LEFT"
        else:
            # Tag por encima de la cruz -> robot hacia la derecha.
            cmd = make_twist(linear_y=-self.cmd_y_sign * vy)
            rule = "TAG_ABOVE_GO_RIGHT"

        duration = abs_y / vy * self.camera_align_position_gain
        duration = clamp(duration, self.camera_align_min_xy_move_sec, self.camera_align_max_xy_move_sec)

        self.last_direct_rule = rule
        return rule, cmd, duration

    def compute_direct_yaw_move(self, tag: AprilTagState) -> Tuple[str, Twist, float]:
        """
        Corrige yaw de forma determinista.
        Si el signo real del giro sale invertido, cambia cmd_w_sign desde el launcher.
        """
        err_rad = normalize_angle_rad(math.radians(tag.err_yaw_deg))
        w = abs(self.camera_align_angular_speed_rad_s)

        if w <= 1e-6 or abs(err_rad) <= math.radians(self.tag_camera.tol_yaw_deg):
            return "NONE", make_twist(), 0.0

        # Si el yaw se corrige al revés en la prueba real, cambia cmd_w_sign a -1.0.
        cmd_w = -1.0 if err_rad > 0.0 else 1.0

        cmd = make_twist(angular_z=self.cmd_w_sign * cmd_w * w)

        duration = abs(err_rad) / w * self.camera_align_position_gain
        duration = clamp(duration, self.camera_align_min_yaw_move_sec, self.camera_align_max_yaw_move_sec)

        return "YAW_DIRECT", cmd, duration

    # ========================================================
    # Fallback antiguo si direct_align_enabled=false
    # ========================================================

    def get_axis_error(self, axis: str, tag: AprilTagState) -> Optional[float]:
        if axis == "X":
            return tag.err_x_m
        if axis == "Y":
            return tag.err_y_m
        if axis == "YAW":
            return tag.err_yaw_rad
        return None

    def select_next_axis_fallback(self, tag: AprilTagState) -> Optional[str]:
        if tag.err_x_m is None or tag.err_y_m is None or tag.err_yaw_rad is None:
            return None

        xy_error = max(abs(tag.err_x_m), abs(tag.err_y_m))

        for axis in self.camera_axis_order:
            if axis == "YAW" and xy_error > self.yaw_only_when_xy_within_m:
                continue

            err = self.get_axis_error(axis, tag)
            if err is None:
                continue
            if axis == "X" and abs(err) > self.tag_camera.tol_x_m:
                return axis
            if axis == "Y" and abs(err) > self.tag_camera.tol_y_m:
                return axis
            if axis == "YAW" and abs(err) > math.radians(self.tag_camera.tol_yaw_deg):
                return axis
        return None

    def compute_metric_move_fallback(self, axis: str, tag: AprilTagState) -> Tuple[Twist, float, Optional[float]]:
        err = self.get_axis_error(axis, tag)

        if err is None:
            return make_twist(), 0.0, None

        if axis == "X":
            speed = abs(self.camera_align_linear_x_speed_m_s)
            sign = self.cmd_x_sign * (1.0 if err > 0.0 else -1.0)
            cmd = make_twist(linear_x=sign * speed)
        elif axis == "Y":
            speed = abs(self.camera_align_linear_y_speed_m_s)
            sign = self.cmd_y_sign * (1.0 if err > 0.0 else -1.0)
            cmd = make_twist(linear_y=sign * speed)
        elif axis == "YAW":
            speed = abs(self.camera_align_angular_speed_rad_s)
            sign = self.cmd_w_sign * (1.0 if err > 0.0 else -1.0)
            cmd = make_twist(angular_z=sign * speed)
        else:
            return make_twist(), 0.0, err

        if speed <= 1e-6:
            duration = 0.0
        else:
            duration = abs(err) / speed
            duration *= self.camera_align_move_gain

        duration = clamp(duration, self.camera_align_min_move_sec, self.camera_align_max_move_sec)
        return cmd, duration, err

    # ========================================================
    # Alignment state handlers
    # ========================================================

    def handle_post_lap_stop(self):
        if self.state != "POST_LAP_STOP":
            return False

        self.publish_zero()

        if self.state_elapsed() >= self.camera_align_initial_stop_sec:
            self.tag_camera.clear_votes()
            self.set_state("CAMERA_EVALUATE")

        return True

    def publish_start_pose_event(self, tag: AprilTagState):
        self.start_pose_event_id += 1
        self.last_start_pose_event_timestamp = time.time()
        self.last_start_pose_event_lap_index = self.completed_laps

        self.log_event(
            "START_POSE_EVENT",
            f"event_id={self.start_pose_event_id} | lap={self.completed_laps} | "
            f"stable_ok={tag.stable_ok} | "
            f"err_m=({self.fmt_m(tag.err_x_m)},{self.fmt_m(tag.err_y_m)},{tag.err_yaw_deg:+.2f}deg)",
            ANSI_GREEN,
            warn=True,
        )
        self.write_patrol_state(force=True)

    def handle_camera_evaluate(self, tag: AprilTagState):
        if self.state != "CAMERA_EVALUATE":
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
            self.log_event(
                "APRILTAG",
                f"START OK after lap={self.completed_laps} | "
                f"votes={tag.ok_votes}/{tag.total_votes} | "
                f"x={self.fmt_m(tag.err_x_m)} y={self.fmt_m(tag.err_y_m)} yaw={tag.err_yaw_deg:+.2f}deg",
                ANSI_GREEN,
                warn=True,
            )

            self.publish_start_pose_event(tag)
            self.tag_camera.clear_votes()
            self.last_camera_axis = "NONE"
            self.last_direct_rule = "NONE"

            if self.repeat_auto:
                self.start_replay()
            else:
                self.set_state("AUTO_DONE")

            return True

        if self.camera_align_attempts >= self.camera_max_align_attempts:
            self.publish_zero()

            self.log_event(
                "APRILTAG",
                f"Máximo de intentos de alineación alcanzado ({self.camera_align_attempts}). "
                f"Me quedo parado para evitar bucles.",
                ANSI_RED,
                warn=True,
            )

            self.set_state("AUTO_DONE")
            return True

        if self.direct_align_enabled:
            if tag.err_x_m is None or tag.err_y_m is None or tag.err_yaw_rad is None:
                self.log_event(
                    "APRILTAG",
                    "No hay escala métrica válida todavía. Esperando.",
                    ANSI_YELLOW,
                    warn=True,
                )
                return True

            # Primero posición, luego yaw. Así no gira si todavía está lejos de la cruz.
            if self.position_first and self.position_needs_correction(tag):
                rule, cmd, duration = self.compute_direct_camera_frame_move(tag)

                if rule == "NONE" or duration <= 0.0:
                    self.log_event("DIRECT_ALIGN", "No hay movimiento XY válido. Espero nueva lectura.", ANSI_YELLOW, warn=True)
                    return True

                self.camera_move_cmd = cmd
                self.camera_move_end_time = time.monotonic() + duration
                self.camera_align_attempts += 1
                self.last_camera_axis = rule

                self.log_event(
                    "DIRECT_MOVE_XY",
                    f"{self.camera_align_attempts}/{self.camera_max_align_attempts} | "
                    f"rule={rule} | "
                    f"err_x={self.fmt_m(tag.err_x_m)} err_y={self.fmt_m(tag.err_y_m)} | "
                    f"cmd=({cmd.linear.x:+.3f},{cmd.linear.y:+.3f},{cmd.angular.z:+.3f}) | "
                    f"duration={duration:.2f}s",
                    ANSI_MAGENTA,
                    warn=True,
                )

                self.set_state("CAMERA_METRIC_MOVE")
                return True

            if self.yaw_needs_correction(tag):
                rule, cmd, duration = self.compute_direct_yaw_move(tag)

                if rule == "NONE" or duration <= 0.0:
                    self.log_event("DIRECT_ALIGN", "No hay movimiento YAW válido. Espero nueva lectura.", ANSI_YELLOW, warn=True)
                    return True

                self.camera_move_cmd = cmd
                self.camera_move_end_time = time.monotonic() + duration
                self.camera_align_attempts += 1
                self.last_camera_axis = rule

                self.log_event(
                    "DIRECT_MOVE_YAW",
                    f"{self.camera_align_attempts}/{self.camera_max_align_attempts} | "
                    f"yaw_err={tag.err_yaw_deg:+.2f}deg | "
                    f"cmd=({cmd.linear.x:+.3f},{cmd.linear.y:+.3f},{cmd.angular.z:+.3f}) | "
                    f"duration={duration:.2f}s",
                    ANSI_MAGENTA,
                    warn=True,
                )

                self.set_state("CAMERA_METRIC_MOVE")
                return True

            # Si se prefiere corregir posición después del yaw, se permite aquí.
            if not self.position_first and self.position_needs_correction(tag):
                rule, cmd, duration = self.compute_direct_camera_frame_move(tag)
                if rule != "NONE" and duration > 0.0:
                    self.camera_move_cmd = cmd
                    self.camera_move_end_time = time.monotonic() + duration
                    self.camera_align_attempts += 1
                    self.last_camera_axis = rule
                    self.log_event(
                        "DIRECT_MOVE_XY",
                        f"{self.camera_align_attempts}/{self.camera_max_align_attempts} | "
                        f"rule={rule} | "
                        f"err_x={self.fmt_m(tag.err_x_m)} err_y={self.fmt_m(tag.err_y_m)} | "
                        f"cmd=({cmd.linear.x:+.3f},{cmd.linear.y:+.3f},{cmd.angular.z:+.3f}) | "
                        f"duration={duration:.2f}s",
                        ANSI_MAGENTA,
                        warn=True,
                    )
                    self.set_state("CAMERA_METRIC_MOVE")
                    return True

            self.log_event(
                "APRILTAG",
                f"Dentro de tolerancia pero esperando votos: votes={tag.ok_votes}/{tag.total_votes}",
                ANSI_YELLOW,
                warn=True,
            )
            return True

        axis = self.select_next_axis_fallback(tag)
        if axis is None:
            self.log_event(
                "APRILTAG",
                f"Sin eje claro que corregir, pero todavía no hay votos suficientes: "
                f"votes={tag.ok_votes}/{tag.total_votes}. Esperando.",
                ANSI_YELLOW,
                warn=True,
            )
            return True

        cmd, duration, err = self.compute_metric_move_fallback(axis, tag)
        self.camera_move_cmd = cmd
        self.camera_move_end_time = time.monotonic() + duration
        self.camera_align_attempts += 1
        self.last_camera_axis = axis

        unit = "m" if axis in {"X", "Y"} else "rad"
        self.log_event(
            "METRIC_MOVE_FALLBACK",
            f"{self.camera_align_attempts}/{self.camera_max_align_attempts} | "
            f"axis={axis} | err={err:+.3f}{unit} | "
            f"x={self.fmt_m(tag.err_x_m)} y={self.fmt_m(tag.err_y_m)} yaw={tag.err_yaw_deg:+.2f}deg | "
            f"cmd=({cmd.linear.x:+.3f},{cmd.linear.y:+.3f},{cmd.angular.z:+.3f}) | "
            f"duration={duration:.2f}s",
            ANSI_MAGENTA,
            warn=True,
        )

        self.set_state("CAMERA_METRIC_MOVE")
        return True

    def handle_camera_metric_move(self):
        if self.state != "CAMERA_METRIC_MOVE":
            return False

        if time.monotonic() < self.camera_move_end_time:
            self.cmd_pub.publish(self.camera_move_cmd)
            return True

        self.publish_zero()
        self.set_state("CAMERA_AFTER_METRIC_STOP")
        return True

    def handle_camera_after_metric_stop(self):
        if self.state != "CAMERA_AFTER_METRIC_STOP":
            return False

        self.publish_zero()

        if self.state_elapsed() >= self.camera_align_hard_stop_sec:
            self.log_event(
                "RECHECK",
                f"Stop terminado. Reevalúo rule={self.last_camera_axis}",
                ANSI_CYAN,
                warn=True,
            )
            self.tag_camera.clear_votes()
            self.set_state("CAMERA_EVALUATE")

        return True

    def handle_wait_zero(self, tag: AprilTagState, key: int):
        if self.state != "WAIT_ZERO":
            return False

        self.publish_zero()

        if key in (ord("c"), ord("C")):
            if not tag.detected:
                self.log_event(
                    "APRILTAG",
                    "Has pulsado C, pero no hay AprilTag detectado.",
                    ANSI_RED,
                    warn=True,
                )
                return True

            if not self.tag_camera.set_zero_from_current_tag():
                self.log_event(
                    "APRILTAG",
                    "No se pudo fijar cero visual.",
                    ANSI_RED,
                    warn=True,
                )
                return True

            self.log_event(
                "APRILTAG",
                f"ZERO SET | x={tag.cx:.1f}px y={tag.cy:.1f}px yaw={tag.yaw_deg:+.1f}deg",
                ANSI_GREEN,
                warn=True,
            )

            if self.mode == "teach":
                self.actions.clear()
                self.current_action_type = "STOP"
                self.current_cmd = make_twist()
                self.current_action_start_time = time.monotonic()

                self.set_state("TEACH_RECORDING")

                self.log_event(
                    "TEACH",
                    "Grabación iniciada. Usa W/A/D/S/Q/E/X para cambiar de fase. Pulsa G para guardar.",
                    ANSI_GREEN,
                    warn=True,
                )

            elif self.mode == "auto":
                self.start_replay()

        return True

    # ========================================================
    # Patrol JSON output
    # ========================================================

    def current_action_payload(self) -> Dict[str, Any]:
        if self.mode != "auto" or not self.loaded_actions or self.current_action_index >= len(self.loaded_actions):
            return {
                "index": None,
                "type": None,
                "elapsed_sec": 0.0,
                "target_duration_sec": 0.0,
                "remaining_sec": 0.0,
                "paused": self.state == "SAFETY_STOP_PERSON",
            }

        action = self.loaded_actions[self.current_action_index]
        elapsed = self.current_auto_action_elapsed()
        target = self.effective_action_duration(action)
        return {
            "index": int(self.current_action_index),
            "type": action.action_type,
            "elapsed_sec": float(elapsed),
            "target_duration_sec": float(target),
            "remaining_sec": float(max(0.0, target - elapsed)),
            "paused": self.state == "SAFETY_STOP_PERSON",
        }

    def apriltag_payload(self, tag: AprilTagState) -> Dict[str, Any]:
        return {
            "detected": bool(tag.detected),
            "calibrated_zero": bool(tag.calibrated_zero),
            "stable_ok": bool(tag.stable_ok),
            "instant_ok": bool(tag.instant_ok),
            "cx": float(tag.cx),
            "cy": float(tag.cy),
            "yaw_deg": float(tag.yaw_deg),
            "target_x": float(tag.target_x),
            "target_y": float(tag.target_y),
            "target_yaw_deg": float(tag.target_yaw_deg),
            "err_x_px": float(tag.err_x_px),
            "err_y_px": float(tag.err_y_px),
            "err_yaw_deg": float(tag.err_yaw_deg),
            "cm_per_px": tag.cm_per_px,
            "err_x_cm": tag.err_x_cm,
            "err_y_cm": tag.err_y_cm,
            "err_x_m": tag.err_x_m,
            "err_y_m": tag.err_y_m,
            "err_yaw_rad": tag.err_yaw_rad,
            "ok_votes": int(tag.ok_votes),
            "total_votes": int(tag.total_votes),
        }

    def patrol_state_payload(self) -> Dict[str, Any]:
        now_wall = time.time()
        return {
            "status": "running" if self.state not in {"AUTO_DONE", "TEACH_DONE"} else "done",
            "mode": self.mode,
            "state": self.state,
            "timestamp": now_wall,
            "wall_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "lap_index": int(self.completed_laps),
            "action": self.current_action_payload(),
            "alignment": {
                "active": self.state in {"POST_LAP_STOP", "CAMERA_EVALUATE", "CAMERA_METRIC_MOVE", "CAMERA_AFTER_METRIC_STOP"},
                "attempts": int(self.camera_align_attempts),
                "last_axis": self.last_camera_axis,
                "cmd": {
                    "linear_x": float(self.camera_move_cmd.linear.x),
                    "linear_y": float(self.camera_move_cmd.linear.y),
                    "angular_z": float(self.camera_move_cmd.angular.z),
                },
                "direct": {
                    "enabled": bool(self.direct_align_enabled),
                    "rule": self.last_direct_rule,
                    "err_x_m": float(self.last_direct_error_x_m),
                    "err_y_m": float(self.last_direct_error_y_m),
                    "mapping": {
                        "tag_left": "forward",
                        "tag_right": "backward",
                        "tag_above": "right",
                        "tag_below": "left",
                    },
                },
                "signs": {
                    "cmd_x_sign": float(self.cmd_x_sign),
                    "cmd_y_sign": float(self.cmd_y_sign),
                    "cmd_w_sign": float(self.cmd_w_sign),
                },
            },
            "safety_stop": {
                "active": self.state == "SAFETY_STOP_PERSON",
                "reason": self.safety_stop_reason,
                "previous_state": self.safety_previous_state,
                "triggered_sources": list(self.triggered_sources),
                "person_detected_now": bool(self.person_detected_now),
                "person_detected_stable": bool(self.person_detected_stable),
                "clear_stable": bool(self.clear_stable),
                "person_stable_sec": float(self.person_stable_sec),
                "clear_stable_sec": float(self.clear_stable_sec),
                "person_confirm_sec": float(self.person_confirm_sec),
                "clear_confirm_sec": float(self.clear_confirm_sec),
            },
            "sensors": {
                "camera": self.sensor_states.get("camera", SensorReadState(enabled=False)).to_dict(),
                "mmwave": self.sensor_states.get("mmwave", SensorReadState(enabled=False)).to_dict(),
                "lidar": self.sensor_states.get("lidar", SensorReadState(enabled=False)).to_dict(),
            },
            "apriltag": self.apriltag_payload(self.last_tag),
            "start_pose_event": {
                "event_id": int(self.start_pose_event_id),
                "active": self.start_pose_event_id > 0,
                "lap_index": int(self.last_start_pose_event_lap_index),
                "timestamp": self.last_start_pose_event_timestamp,
            },
        }

    def write_patrol_state(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self.last_patrol_state_write_mono < self.patrol_state_write_period_sec:
            return
        self.last_patrol_state_write_mono = now

        try:
            atomic_write_json(self.patrol_state_path, self.patrol_state_payload())
        except Exception as exc:
            self.get_logger().warn(f"No se pudo escribir patrol_state_json: {exc}", throttle_duration_sec=2.0)

    # ========================================================
    # Main timer
    # ========================================================

    def on_timer(self):
        with self.lock:
            try:
                tag, key = self.tag_camera.process_once()
                self.last_tag = tag

                self.update_sensor_states()

                if key == 27:
                    self.publish_zero()
                    self.log_event("EMERGENCY", "ESC pulsado. Parando.", ANSI_RED, warn=True)
                    rclpy.shutdown()
                    return

                if self.handle_wait_zero(tag, key):
                    return

                if self.mode == "teach":
                    self.handle_teach_key(key)

                    if self.state == "TEACH_RECORDING":
                        self.cmd_pub.publish(self.current_cmd)
                        return

                    if self.state == "TEACH_DONE":
                        self.publish_zero()
                        return

                if self.mode == "auto":
                    if self.handle_safety_stop():
                        return

                    if self.maybe_enter_safety_stop():
                        return

                    if self.handle_auto_action():
                        return

                    if self.handle_between_actions():
                        return

                    if self.handle_post_lap_stop():
                        return

                    if self.handle_camera_evaluate(tag):
                        return

                    if self.handle_camera_metric_move():
                        return

                    if self.handle_camera_after_metric_stop():
                        return

                    if self.state == "AUTO_DONE":
                        self.publish_zero()
                        return

                self.publish_zero()

            except KeyboardInterrupt:
                self.publish_zero()
                rclpy.shutdown()
                return
            except Exception as e:
                self.log_event("ERROR", f"Error en on_timer: {e}", ANSI_RED, warn=True)
                self.publish_zero()
                return
            finally:
                self.write_patrol_state()

    def destroy_node(self):
        self.publish_zero()

        try:
            self.write_patrol_state(force=True)
        except Exception:
            pass

        try:
            self.tag_camera.close()
        except Exception:
            pass

        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)

    node = PhaseTeachReplayPatrol()

    try:
        rclpy.spin(node)
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


if __name__ == "__main__":
    main()
