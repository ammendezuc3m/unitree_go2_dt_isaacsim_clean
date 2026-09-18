#!/usr/bin/env python3
import json
import math
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry


# ============================================================
# MAP / ROUTE CONFIG
# ============================================================

ROWS = 10
COLS = 8
DEFAULT_TILE_SIZE_M = 0.60

# Punto inicial
START_TILE = (1, 6)

# Ruta "macro" en sentido antihorario
LOOP_WAYPOINTS_CCW: List[Tuple[int, int]] = [
    (1, 6),
    (1, 7),
    (6, 7),
    (6, 2),
    (3, 2),
    (3, 4),
    (2, 4),
    (2, 5),
    (1, 5),
    (1, 6),
]

# Celdas prohibidas / no transitables del escenario
STATIC_WALLS: Set[Tuple[int, int]] = {
    (3, 1), (4, 1), (5, 1), (6, 1),
    (7, 2), (7, 3), (7, 4), (7, 5), (7, 6),
    (3, 3),
    (3, 5), (6, 5),
}


# ============================================================
# SENSOR JSON PATHS
# ============================================================

DEFAULT_MMWAVE_JSON = "/home/nextnet/AlbertoDir/go2_dt/csi_dog_dataset_20210421_181125/analysis_outputs/live_prediction_state.json"
DEFAULT_CAMERA_JSON = "/home/nextnet/AlbertoDir/go2_dt/camera_yolo/outputs/live_camera_state.json"
DEFAULT_FIVEG_JSON = "/home/nextnet/AlbertoDir/go2_dt/5g_sensing_part/outputs/live_5g_state.json"

MMWAVE_OCCUPIED_LABELS = {
    "person",
    "person_1",
    "occupied",
    "true",
}


# ============================================================
# ZONES
# ============================================================

ZONE_ORANGE = "orange"   # 5G
ZONE_GREEN = "green"     # mmWave
ZONE_BLUE = "blue"       # cámara


# ============================================================
# HELPERS
# ============================================================

def normalize_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def inside(tile: Tuple[int, int]) -> bool:
    x, y = tile
    return 0 <= x < COLS and 0 <= y < ROWS


def sign(x: int) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def expand_axis_aligned_polyline(points: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    if not points:
        return []

    out = [points[0]]

    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]

        dx = sign(x1 - x0)
        dy = sign(y1 - y0)

        if dx != 0 and dy != 0:
            raise ValueError(f"Segmento no ortogonal: {points[i]} -> {points[i+1]}")

        cx, cy = x0, y0
        while (cx, cy) != (x1, y1):
            cx += dx
            cy += dy
            out.append((cx, cy))

    return out


def unique_cyclic_path(path_with_last_equal_first: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    if len(path_with_last_equal_first) >= 2 and path_with_last_equal_first[0] == path_with_last_equal_first[-1]:
        return path_with_last_equal_first[:-1]
    return list(path_with_last_equal_first)


@dataclass
class Pose2D:
    x: float
    y: float
    yaw: float


@dataclass
class SensorState:
    online: bool
    occupied: bool
    mtime: Optional[float]
    raw: Dict


# ============================================================
# NODE
# ============================================================

class ZoneLoopPatrolNode(Node):
    def __init__(self):
        super().__init__("zone_loop_patrol")

        # ---------------- Parameters ----------------
        self.declare_parameter("cmd_topic", "/cmd_vel_out")
        self.declare_parameter("odom_topic", "/odom")

        self.declare_parameter("mmwave_json", DEFAULT_MMWAVE_JSON)
        self.declare_parameter("camera_json", DEFAULT_CAMERA_JSON)
        self.declare_parameter("fiveg_json", DEFAULT_FIVEG_JSON)

        self.declare_parameter("loop_hz", 20.0)
        self.declare_parameter("sensor_poll_hz", 5.0)

        self.declare_parameter("linear_speed", 0.10)
        self.declare_parameter("angular_speed", 0.35)
        self.declare_parameter("tile_size", DEFAULT_TILE_SIZE_M)

        self.declare_parameter("waypoint_pos_tolerance", 0.08)
        self.declare_parameter("rotate_tolerance_deg", 6.0)
        self.declare_parameter("move_yaw_gate_deg", 14.0)
        self.declare_parameter("max_angular_correction", 0.18)
        self.declare_parameter("segment_completion_margin", 0.08)

        self.declare_parameter("sensor_stale_sec", 2.0)
        self.declare_parameter("reverse_cooldown_sec", 0.7)
        self.declare_parameter("confirm_blocked_sec", 0.15)

        self.cmd_topic = self.get_parameter("cmd_topic").get_parameter_value().string_value
        self.odom_topic = self.get_parameter("odom_topic").get_parameter_value().string_value

        self.mmwave_json = self.get_parameter("mmwave_json").get_parameter_value().string_value
        self.camera_json = self.get_parameter("camera_json").get_parameter_value().string_value
        self.fiveg_json = self.get_parameter("fiveg_json").get_parameter_value().string_value

        self.loop_hz = self.get_parameter("loop_hz").get_parameter_value().double_value
        self.sensor_poll_hz = self.get_parameter("sensor_poll_hz").get_parameter_value().double_value

        self.linear_speed = self.get_parameter("linear_speed").get_parameter_value().double_value
        self.angular_speed = self.get_parameter("angular_speed").get_parameter_value().double_value
        self.tile_size = self.get_parameter("tile_size").get_parameter_value().double_value

        self.waypoint_pos_tolerance = self.get_parameter("waypoint_pos_tolerance").get_parameter_value().double_value
        self.rotate_tolerance = math.radians(
            self.get_parameter("rotate_tolerance_deg").get_parameter_value().double_value
        )
        self.move_yaw_gate = math.radians(
            self.get_parameter("move_yaw_gate_deg").get_parameter_value().double_value
        )
        self.max_angular_correction = self.get_parameter("max_angular_correction").get_parameter_value().double_value
        self.segment_completion_margin = self.get_parameter("segment_completion_margin").get_parameter_value().double_value

        self.sensor_stale_sec = self.get_parameter("sensor_stale_sec").get_parameter_value().double_value
        self.reverse_cooldown_sec = self.get_parameter("reverse_cooldown_sec").get_parameter_value().double_value
        self.confirm_blocked_sec = self.get_parameter("confirm_blocked_sec").get_parameter_value().double_value

        # ---------------- ROS ----------------
        self.cmd_pub = self.create_publisher(Twist, self.cmd_topic, 10)
        self.odom_sub = self.create_subscription(Odometry, self.odom_topic, self.odom_callback, 20)

        self.sensor_timer = self.create_timer(1.0 / self.sensor_poll_hz, self.poll_sensor_states)
        self.loop_timer = self.create_timer(1.0 / self.loop_hz, self.control_loop)

        # ---------------- Pose / anchor ----------------
        self.current_pose: Optional[Pose2D] = None
        self.anchor_pose: Optional[Pose2D] = None
        self.forward_vec: Optional[Tuple[float, float]] = None
        self.right_vec: Optional[Tuple[float, float]] = None

        # ---------------- Route ----------------
        expanded = expand_axis_aligned_polyline(LOOP_WAYPOINTS_CCW)
        self.loop_tiles: List[Tuple[int, int]] = unique_cyclic_path(expanded)

        if self.loop_tiles[0] != START_TILE:
            raise RuntimeError(f"La ruta no empieza en START_TILE={START_TILE}")

        for t in self.loop_tiles:
            if not inside(t):
                raise RuntimeError(f"Tile fuera del mapa: {t}")

        # Sentido actual: +1 = antihorario, -1 = horario
        self.direction = +1

        # Índice actual del tile en el lazo
        self.current_index = 0
        self.next_index = self.wrap_index(self.current_index + self.direction)

        self.motion_state = "ROTATE_TO_EDGE"
        self.latched_target_yaw: Optional[float] = None

        # ---------------- Sensors ----------------
        self.mmwave_state = SensorState(False, False, None, {})
        self.camera_state = SensorState(False, False, None, {})
        self.fiveg_state = SensorState(False, False, None, {})

        # ---------------- Reverse / blocking FSM ----------------
        self.last_reverse_time = 0.0
        self.block_candidate_since: Optional[float] = None
        self.block_candidate_reason: Optional[str] = None

        # ---------------- Zones on path ----------------
        self.green_tiles: Set[Tuple[int, int]] = {
            (6, 7), (6, 6), (6, 5), (6, 4), (6, 3), (6, 2),
        }

        self.blue_tiles: Set[Tuple[int, int]] = {
            (5, 2), (4, 2), (3, 2),
        }

        self.orange_tiles: Set[Tuple[int, int]] = set(self.loop_tiles) - self.green_tiles - self.blue_tiles

        self.get_logger().info(f"Loop tiles ({len(self.loop_tiles)}): {self.loop_tiles}")
        self.get_logger().info(f"Start tile={START_TILE}, initial direction=CCW")
        self.get_logger().info(f"green_tiles={sorted(self.green_tiles)}")
        self.get_logger().info(f"blue_tiles={sorted(self.blue_tiles)}")
        self.get_logger().info(f"orange_tiles={sorted(self.orange_tiles)}")

    # --------------------------------------------------------
    # Basic route helpers
    # --------------------------------------------------------

    def wrap_index(self, idx: int) -> int:
        return idx % len(self.loop_tiles)

    def current_tile(self) -> Tuple[int, int]:
        return self.loop_tiles[self.current_index]

    def target_tile(self) -> Tuple[int, int]:
        return self.loop_tiles[self.next_index]

    def zone_for_tile(self, tile: Tuple[int, int]) -> str:
        if tile in self.green_tiles:
            return ZONE_GREEN
        if tile in self.blue_tiles:
            return ZONE_BLUE
        return ZONE_ORANGE

    def reverse_direction(self, reason: str):
        now = time.monotonic()
        self.last_reverse_time = now
        self.direction *= -1
        self.next_index = self.wrap_index(self.current_index + self.direction)
        self.motion_state = "ROTATE_TO_EDGE"
        self.latched_target_yaw = None
        self.publish_zero()
        self.get_logger().warn(
            f"[REV] Direction reversed. reason={reason} "
            f"current_tile={self.current_tile()} new_target={self.target_tile()} "
            f"new_dir={'CCW' if self.direction > 0 else 'CW'}"
        )

    # --------------------------------------------------------
    # ROS callbacks
    # --------------------------------------------------------

    def odom_callback(self, msg: Odometry):
        q = msg.pose.pose.orientation
        pose = Pose2D(
            x=msg.pose.pose.position.x,
            y=msg.pose.pose.position.y,
            yaw=yaw_from_quaternion(q.x, q.y, q.z, q.w),
        )
        self.current_pose = pose

        if self.anchor_pose is None:
            self.anchor_pose = pose
            self.forward_vec = (math.cos(pose.yaw), math.sin(pose.yaw))
            self.right_vec = (math.sin(pose.yaw), -math.cos(pose.yaw))
            self.get_logger().info(
                f"Anchor pose fixed at x={pose.x:.3f}, y={pose.y:.3f}, yaw={math.degrees(pose.yaw):.1f} deg"
            )
            self.get_logger().info(
                "Robot starts facing right in the real world; first route step will rotate toward the first edge."
            )

    # --------------------------------------------------------
    # Tile/world transforms
    # --------------------------------------------------------

    def tile_to_world(self, tile: Tuple[int, int]) -> Tuple[float, float]:
        if self.anchor_pose is None or self.forward_vec is None or self.right_vec is None:
            raise RuntimeError("Anchor pose not initialized yet.")

        tx, ty = tile
        dx_tiles = tx - START_TILE[0]
        dy_tiles = ty - START_TILE[1]

        dx_m = dx_tiles * self.tile_size
        dy_m = dy_tiles * self.tile_size

        wx = self.anchor_pose.x + dx_m * self.forward_vec[0] + dy_m * self.right_vec[0]
        wy = self.anchor_pose.y + dx_m * self.forward_vec[1] + dy_m * self.right_vec[1]
        return wx, wy

    def world_to_tile_float(self, x: float, y: float) -> Tuple[float, float]:
        if self.anchor_pose is None or self.forward_vec is None or self.right_vec is None:
            raise RuntimeError("Anchor pose not initialized yet.")

        dx = x - self.anchor_pose.x
        dy = y - self.anchor_pose.y

        proj_forward = dx * self.forward_vec[0] + dy * self.forward_vec[1]
        proj_right = dx * self.right_vec[0] + dy * self.right_vec[1]

        tx = START_TILE[0] + (proj_forward / self.tile_size)
        ty = START_TILE[1] + (proj_right / self.tile_size)
        return tx, ty

    def current_snapped_tile(self) -> Optional[Tuple[int, int]]:
        if self.current_pose is None or self.anchor_pose is None:
            return None

        tx_f, ty_f = self.world_to_tile_float(self.current_pose.x, self.current_pose.y)
        tx = int(round(tx_f))
        ty = int(round(ty_f))
        tile = (tx, ty)

        if not inside(tile):
            return None
        return tile

    # --------------------------------------------------------
    # Heading exacto por salto de baldosa
    # --------------------------------------------------------

    def heading_for_edge(self, from_tile: Tuple[int, int], to_tile: Tuple[int, int]) -> float:
        if self.anchor_pose is None:
            raise RuntimeError("Anchor pose not initialized yet.")

        dx = to_tile[0] - from_tile[0]
        dy = to_tile[1] - from_tile[1]
        anchor_yaw = self.anchor_pose.yaw

        if dx == 1 and dy == 0:
            return normalize_angle(anchor_yaw)
        if dx == -1 and dy == 0:
            return normalize_angle(anchor_yaw + math.pi)
        if dx == 0 and dy == 1:
            return normalize_angle(anchor_yaw - math.pi / 2.0)
        if dx == 0 and dy == -1:
            return normalize_angle(anchor_yaw + math.pi / 2.0)

        raise RuntimeError(f"Invalid grid edge from {from_tile} to {to_tile}")

    # --------------------------------------------------------
    # Sensor JSON readers
    # --------------------------------------------------------

    def _json_mtime(self, path: str) -> Optional[float]:
        try:
            return os.path.getmtime(path) if os.path.exists(path) else None
        except Exception:
            return None

    def _read_json(self, path: str) -> Dict:
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            self.get_logger().warn(f"Could not read JSON {path}: {e}")
            return {}

    def read_mmwave_state(self) -> SensorState:
        mtime = self._json_mtime(self.mmwave_json)
        if mtime is None:
            return SensorState(False, False, None, {})

        age = time.time() - mtime
        data = self._read_json(self.mmwave_json)
        if age > self.sensor_stale_sec:
            return SensorState(False, False, mtime, data)

        label = str(data.get("prediction", "")).strip().lower()
        occupied = label in MMWAVE_OCCUPIED_LABELS
        return SensorState(True, occupied, mtime, data)

    def read_camera_state(self) -> SensorState:
        mtime = self._json_mtime(self.camera_json)
        if mtime is None:
            return SensorState(False, False, None, {})

        age = time.time() - mtime
        data = self._read_json(self.camera_json)
        if age > self.sensor_stale_sec:
            return SensorState(False, False, mtime, data)

        status = str(data.get("status", "online")).strip().lower()
        person_detected = bool(data.get("person_detected", False))
        count = int(data.get("count", 0) or 0)
        occupied = person_detected or count > 0
        online = status not in {"fault", "offline", "stale"}
        return SensorState(online, occupied if online else False, mtime, data)

    def read_fiveg_state(self) -> SensorState:
        mtime = self._json_mtime(self.fiveg_json)
        if mtime is None:
            return SensorState(False, False, None, {})

        age = time.time() - mtime
        data = self._read_json(self.fiveg_json)
        if age > self.sensor_stale_sec:
            return SensorState(False, False, mtime, data)

        status = str(data.get("status", "online")).strip().lower()
        person_detected = bool(data.get("person_detected", False))
        count = int(data.get("count", 0) or 0)
        occupied = person_detected or count > 0
        online = status not in {"fault", "offline", "stale"}
        return SensorState(online, occupied if online else False, mtime, data)

    def poll_sensor_states(self):
        self.mmwave_state = self.read_mmwave_state()
        self.camera_state = self.read_camera_state()
        self.fiveg_state = self.read_fiveg_state()

    def zone_blocked(self, zone: str) -> bool:
        if zone == ZONE_GREEN:
            return self.mmwave_state.online and self.mmwave_state.occupied
        if zone == ZONE_BLUE:
            return self.camera_state.online and self.camera_state.occupied
        if zone == ZONE_ORANGE:
            return self.fiveg_state.online and self.fiveg_state.occupied
        return False

    # --------------------------------------------------------
    # Motion helpers
    # --------------------------------------------------------

    def publish_zero(self):
        try:
            if rclpy.ok():
                self.cmd_pub.publish(Twist())
        except Exception:
            pass

    def distance_to_world_point(self, wx: float, wy: float) -> float:
        if self.current_pose is None:
            return float("inf")
        dx = wx - self.current_pose.x
        dy = wy - self.current_pose.y
        return math.hypot(dx, dy)

    def segment_progress_and_length(
        self,
        prev_tile: Tuple[int, int],
        target_tile: Tuple[int, int],
    ) -> Tuple[float, float]:
        if self.current_pose is None:
            return 0.0, self.tile_size

        x0, y0 = self.tile_to_world(prev_tile)
        x1, y1 = self.tile_to_world(target_tile)

        sx = x1 - x0
        sy = y1 - y0
        seg_len = math.hypot(sx, sy)
        if seg_len < 1e-9:
            return 0.0, 0.0

        rx = self.current_pose.x - x0
        ry = self.current_pose.y - y0

        ux = sx / seg_len
        uy = sy / seg_len

        progress = rx * ux + ry * uy
        return progress, seg_len

    def waypoint_reached(
        self,
        prev_tile: Tuple[int, int],
        target_tile: Tuple[int, int],
        dist_to_target: float,
    ) -> bool:
        if dist_to_target <= self.waypoint_pos_tolerance:
            return True

        snapped = self.current_snapped_tile()
        if snapped == target_tile:
            return True

        progress, seg_len = self.segment_progress_and_length(prev_tile, target_tile)

        if seg_len > 1e-9 and progress >= (seg_len - self.segment_completion_margin):
            return True

        if seg_len > 1e-9 and progress >= seg_len:
            return True

        return False

    # --------------------------------------------------------
    # Decision logic
    # --------------------------------------------------------

    def blocked_reason_now(self) -> Optional[str]:
        now = time.monotonic()
        if now - self.last_reverse_time < self.reverse_cooldown_sec:
            return None

        curr_tile = self.current_tile()
        next_tile = self.target_tile()

        curr_zone = self.zone_for_tile(curr_tile)
        next_zone = self.zone_for_tile(next_tile)

        # Si estoy en naranja y 5G detecta persona, doy la vuelta
        if curr_zone == ZONE_ORANGE and self.zone_blocked(ZONE_ORANGE):
            return f"orange_blocked_by_5g current_tile={curr_tile}"

        # Si voy a entrar a otra zona y esa zona está ocupada, doy la vuelta
        if next_zone != curr_zone and self.zone_blocked(next_zone):
            return f"next_zone_blocked next_zone={next_zone} from_tile={curr_tile} to_tile={next_tile}"

        return None

    def process_blocking_logic(self) -> bool:
        reason = self.blocked_reason_now()

        if reason is None:
            self.block_candidate_since = None
            self.block_candidate_reason = None
            return False

        self.publish_zero()

        if self.block_candidate_reason != reason:
            self.block_candidate_reason = reason
            self.block_candidate_since = time.monotonic()
            self.get_logger().warn(f"[OBS] block candidate: {reason}")
            return True

        elapsed = time.monotonic() - (self.block_candidate_since or time.monotonic())
        if elapsed < self.confirm_blocked_sec:
            self.get_logger().info(
                f"[OBS] confirming block... {elapsed:.2f}/{self.confirm_blocked_sec:.2f}s reason={reason}",
                throttle_duration_sec=0.5,
            )
            return True

        self.block_candidate_since = None
        self.block_candidate_reason = None
        self.reverse_direction(reason)
        return True

    # --------------------------------------------------------
    # Main controller
    # --------------------------------------------------------

    def control_loop(self):
        if self.current_pose is None or self.anchor_pose is None:
            self.get_logger().info("Waiting for odometry anchor...", throttle_duration_sec=2.0)
            return

        snapped = self.current_snapped_tile()
        if snapped in self.loop_tiles:
            snapped_idx = self.loop_tiles.index(snapped)
            if snapped_idx != self.current_index:
                pass

        if self.process_blocking_logic():
            return

        prev_tile = self.current_tile()
        target_tile = self.target_tile()

        wx, wy = self.tile_to_world(target_tile)
        dist = self.distance_to_world_point(wx, wy)
        progress, seg_len = self.segment_progress_and_length(prev_tile, target_tile)

        self.get_logger().info(
            f"[CTRL] dir={'CCW' if self.direction > 0 else 'CW'} "
            f"prev_tile={prev_tile} target_tile={target_tile} "
            f"curr_zone={self.zone_for_tile(prev_tile)} next_zone={self.zone_for_tile(target_tile)} "
            f"mmwave={self.mmwave_state.occupied if self.mmwave_state.online else 'off'} "
            f"camera={self.camera_state.occupied if self.camera_state.online else 'off'} "
            f"fiveg={self.fiveg_state.occupied if self.fiveg_state.online else 'off'} "
            f"dist={dist:.3f} progress={progress:.3f}/{seg_len:.3f} state={self.motion_state}",
            throttle_duration_sec=1.0,
        )

        if self.waypoint_reached(prev_tile, target_tile, dist):
            self.current_index = self.next_index
            self.next_index = self.wrap_index(self.current_index + self.direction)
            self.motion_state = "ROTATE_TO_EDGE"
            self.latched_target_yaw = None
            self.publish_zero()

            self.get_logger().info(
                f"Reached tile={self.current_tile()}, next_target={self.target_tile()} "
                f"dir={'CCW' if self.direction > 0 else 'CW'}"
            )
            return

        if self.motion_state == "ROTATE_TO_EDGE":
            if self.latched_target_yaw is None:
                self.latched_target_yaw = self.heading_for_edge(prev_tile, target_tile)
                self.get_logger().info(
                    f"[ROT] edge={prev_tile}->{target_tile} "
                    f"target_yaw={math.degrees(self.latched_target_yaw):.1f} deg"
                )

            yaw_error = normalize_angle(self.latched_target_yaw - self.current_pose.yaw)

            if abs(yaw_error) <= self.rotate_tolerance:
                self.motion_state = "MOVE_TO_EDGE"
                self.publish_zero()
                self.get_logger().info(f"[ROT] aligned. Switching to MOVE.")
                return

            cmd = Twist()
            cmd.linear.x = 0.0
            cmd.angular.z = self.angular_speed if yaw_error > 0.0 else -self.angular_speed
            self.cmd_pub.publish(cmd)
            return

        if self.motion_state == "MOVE_TO_EDGE":
            target_yaw = self.heading_for_edge(prev_tile, target_tile)
            yaw_error = normalize_angle(target_yaw - self.current_pose.yaw)

            if abs(yaw_error) > self.move_yaw_gate:
                self.motion_state = "ROTATE_TO_EDGE"
                self.latched_target_yaw = target_yaw
                self.publish_zero()
                self.get_logger().warn(
                    f"[MOVE] Large yaw drift ({math.degrees(yaw_error):.1f} deg). Re-rotating."
                )
                return

            cmd = Twist()
            cmd.linear.x = self.linear_speed
            correction = max(
                -self.max_angular_correction,
                min(self.max_angular_correction, 1.2 * yaw_error)
            )
            cmd.angular.z = correction
            self.cmd_pub.publish(cmd)
            return

        self.get_logger().warn(f"Unknown motion state: {self.motion_state}. Forcing stop.")
        self.publish_zero()

    def destroy_node(self):
        self.publish_zero()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = ZoneLoopPatrolNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node.publish_zero()
            node.destroy_node()
        except Exception:
            pass
        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()


