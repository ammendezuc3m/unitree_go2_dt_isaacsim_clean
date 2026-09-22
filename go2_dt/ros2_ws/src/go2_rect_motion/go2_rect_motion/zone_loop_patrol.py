#!/usr/bin/env python3
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
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

START_TILE = (3, 2)

LOOP_WAYPOINTS_CCW: List[Tuple[int, int]] = [
    (3, 2),
    (3, 4),
    (0, 4),
    (0, 0),
    (3, 0),
    (3, 2),
]

STATIC_WALLS: Set[Tuple[int, int]] = {
    (1, 1), (1, 2), (2, 2),
}


# ============================================================
# SENSOR JSON PATHS
# ============================================================

GO2_ROOT = Path(__file__).resolve().parents[4]
ROS_WS_ROOT = GO2_ROOT / "ros2_ws"

DEFAULT_MMWAVE_JSON = str(GO2_ROOT / "csi_dog_dataset_20210421_181125" / "analysis_outputs" / "live_prediction_state.json")
DEFAULT_CAMERA_JSON = str(GO2_ROOT / "camera_yolo" / "outputs" / "live_camera_state.json")
DEFAULT_LIDAR_JSON = str(ROS_WS_ROOT / "lidar_outputs" / "live_lidar_state.json")

MMWAVE_OCCUPIED_LABELS = {
    "person",
    "person_1",
    "occupied",
    "true",
}

FALSE_LABELS = {
    "",
    "0",
    "false",
    "no",
    "off",
    "clear",
    "empty",
    "none",
    "nothing",
}


# ============================================================
# ZONES
# ============================================================

ZONE_LIDAR = "lidar"
ZONE_MMWAVE = "mmwave"
ZONE_CAMERA = "camera"


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


def infer_detection_bool(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in FALSE_LABELS:
            return False
        if lowered in MMWAVE_OCCUPIED_LABELS or lowered in {"detected", "human", "person_detected"}:
            return True
    return None


@dataclass
class Pose2D:
    x: float
    y: float
    yaw: float
    yaw_rate: float = 0.0


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

        self.declare_parameter("loop_hz", 20.0)
        self.declare_parameter("sensor_poll_hz", 5.0)

        self.declare_parameter("linear_speed", 0.10)
        self.declare_parameter("angular_speed", 0.35)
        self.declare_parameter("tile_size", DEFAULT_TILE_SIZE_M)

        self.declare_parameter("waypoint_pos_tolerance", 0.08)
        self.declare_parameter("segment_completion_margin", 0.08)
        self.declare_parameter("max_angular_correction", 0.12)

        self.declare_parameter("sensor_stale_sec", 2.0)
        self.declare_parameter("reverse_cooldown_sec", 0.7)
        self.declare_parameter("confirm_blocked_sec", 3.0)

        self.cmd_topic = self.get_parameter("cmd_topic").get_parameter_value().string_value
        self.odom_topic = self.get_parameter("odom_topic").get_parameter_value().string_value

        self.mmwave_json = DEFAULT_MMWAVE_JSON
        self.camera_json = DEFAULT_CAMERA_JSON
        self.lidar_json = DEFAULT_LIDAR_JSON

        self.loop_hz = self.get_parameter("loop_hz").get_parameter_value().double_value
        self.sensor_poll_hz = self.get_parameter("sensor_poll_hz").get_parameter_value().double_value

        self.linear_speed = self.get_parameter("linear_speed").get_parameter_value().double_value
        self.angular_speed = self.get_parameter("angular_speed").get_parameter_value().double_value
        self.tile_size = self.get_parameter("tile_size").get_parameter_value().double_value

        self.waypoint_pos_tolerance = self.get_parameter("waypoint_pos_tolerance").get_parameter_value().double_value
        self.segment_completion_margin = self.get_parameter("segment_completion_margin").get_parameter_value().double_value
        self.max_angular_correction = self.get_parameter("max_angular_correction").get_parameter_value().double_value

        self.sensor_stale_sec = self.get_parameter("sensor_stale_sec").get_parameter_value().double_value
        self.reverse_cooldown_sec = self.get_parameter("reverse_cooldown_sec").get_parameter_value().double_value
        self.confirm_blocked_sec = self.get_parameter("confirm_blocked_sec").get_parameter_value().double_value

        # Epsilon interno mínimo para cerrar el giro
        self.stop_yaw_epsilon = math.radians(1.5)

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

        self.direction = +1   # +1 CCW, -1 CW

        self.current_index = 0
        self.next_index = self.wrap_index(self.current_index + self.direction)

        self.motion_state = "ROTATE_TO_EDGE"
        self.latched_target_yaw: Optional[float] = None

        # ---------------- Sensors ----------------
        self.mmwave_state = SensorState(False, False, None, {})
        self.camera_state = SensorState(False, False, None, {})
        self.lidar_state = SensorState(False, False, None, {})

        # ---------------- Reverse / blocking FSM ----------------
        self.last_reverse_time = 0.0
        self.block_candidate_since: Optional[float] = None
        self.block_candidate_reason: Optional[str] = None

        # ---------------- Zones on path ----------------
        # Sensor ownership on the patrol loop:
        # mmWave only controls the left vertical branch, including (0, 0).
        # Camera controls the bottom branch from (1, 0) to (3, 0).
        # LiDAR controls the rest of the route.
        self.mmwave_tiles: Set[Tuple[int, int]] = {
            (0, 4), (0, 3), (0, 2), (0, 1), (0, 0),
        }

        self.camera_tiles: Set[Tuple[int, int]] = {
            (1, 0), (2, 0), (3, 0),
        }

        self.lidar_tiles: Set[Tuple[int, int]] = {
            t for t in self.loop_tiles
            if t not in self.mmwave_tiles and t not in self.camera_tiles
        }

        self.get_logger().info(f"Loop tiles ({len(self.loop_tiles)}): {self.loop_tiles}")
        self.get_logger().info(f"Start tile={START_TILE}, initial direction=CCW")
        self.get_logger().info(f"mmwave_json={self.mmwave_json}")
        self.get_logger().info(f"camera_json={self.camera_json}")
        self.get_logger().info(f"lidar_json={self.lidar_json}")
        self.get_logger().info(f"mmwave_tiles={sorted(self.mmwave_tiles)}")
        self.get_logger().info(f"camera_tiles={sorted(self.camera_tiles)}")
        self.get_logger().info(f"lidar_tiles={sorted(self.lidar_tiles)}")

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
        if tile in self.mmwave_tiles:
            return ZONE_MMWAVE
        if tile in self.camera_tiles:
            return ZONE_CAMERA
        return ZONE_LIDAR

    def reverse_direction(self, reason: str):
        self.last_reverse_time = time.monotonic()
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
            yaw_rate=msg.twist.twist.angular.z,
        )
        self.current_pose = pose

        if self.anchor_pose is None:
            self.anchor_pose = pose
            self.forward_vec = (math.cos(pose.yaw), math.sin(pose.yaw))
            self.right_vec = (math.sin(pose.yaw), -math.cos(pose.yaw))
            self.get_logger().info(
                f"Anchor pose fixed at x={pose.x:.3f}, y={pose.y:.3f}, yaw={math.degrees(pose.yaw):.1f} deg"
            )

    # --------------------------------------------------------
    # Tile/world transforms
    # --------------------------------------------------------

    def tile_to_world(self, tile: Tuple[int, int]) -> Tuple[float, float]:
        if self.anchor_pose is None or self.forward_vec is None or self.right_vec is None:
            raise RuntimeError("Anchor pose not initialized yet.")

        tx, ty = tile

        dx_tiles = START_TILE[0] - tx
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

        tx = START_TILE[0] - (proj_forward / self.tile_size)
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
    # Heading
    # --------------------------------------------------------

    def heading_for_edge(self, from_tile: Tuple[int, int], to_tile: Tuple[int, int]) -> float:
        dx = to_tile[0] - from_tile[0]
        dy = to_tile[1] - from_tile[1]
        if abs(dx) + abs(dy) != 1:
            raise RuntimeError(f"Invalid grid edge from {from_tile} to {to_tile}")

        x0, y0 = self.tile_to_world(from_tile)
        x1, y1 = self.tile_to_world(to_tile)
        return math.atan2(y1 - y0, x1 - x0)

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

        status = str(data.get("status", "online")).strip().lower()
        online = status not in {"fault", "offline", "stale"}

        occupied_flag = None
        for key in ("person_detected", "occupied", "detected", "has_person"):
            if key in data:
                occupied_flag = infer_detection_bool(data.get(key))
                if occupied_flag is not None:
                    break

        if occupied_flag is None:
            for key in ("prediction", "raw_prediction", "label", "state"):
                if key in data:
                    occupied_flag = infer_detection_bool(data.get(key))
                    if occupied_flag is not None:
                        break

        count = int(data.get("count", 0) or 0)
        occupied = bool(occupied_flag) or count > 0
        return SensorState(online, occupied if online else False, mtime, data)

    def read_camera_state(self) -> SensorState:
        mtime = self._json_mtime(self.camera_json)
        if mtime is None:
            return SensorState(False, False, None, {})

        age = time.time() - mtime
        data = self._read_json(self.camera_json)
        if age > self.sensor_stale_sec:
            return SensorState(False, False, mtime, data)

        status = str(data.get("status", "online")).strip().lower()
        person_detected = bool(infer_detection_bool(data.get("person_detected", False)))
        count = int(data.get("count", 0) or 0)
        occupied = person_detected or count > 0
        online = status not in {"fault", "offline", "stale"}
        return SensorState(online, occupied if online else False, mtime, data)

    def read_lidar_state(self) -> SensorState:
        mtime = self._json_mtime(self.lidar_json)
        if mtime is None:
            return SensorState(False, False, None, {})

        age = time.time() - mtime
        data = self._read_json(self.lidar_json)
        if age > self.sensor_stale_sec:
            return SensorState(False, False, mtime, data)

        status = str(data.get("status", "online")).strip().lower()
        person_detected = bool(infer_detection_bool(data.get("person_detected", False)))
        count = int(data.get("count", 0) or 0)
        occupied = person_detected or count > 0
        online = status not in {"fault", "offline", "stale"}
        return SensorState(online, occupied if online else False, mtime, data)

    def poll_sensor_states(self):
        self.mmwave_state = self.read_mmwave_state()
        self.camera_state = self.read_camera_state()
        self.lidar_state = self.read_lidar_state()

    def zone_blocked(self, zone: str) -> bool:
        if zone == ZONE_MMWAVE:
            return self.mmwave_state.online and self.mmwave_state.occupied
        if zone == ZONE_CAMERA:
            return self.camera_state.online and self.camera_state.occupied
        if zone == ZONE_LIDAR:
            return self.lidar_state.online and self.lidar_state.occupied
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

        if self.zone_blocked(curr_zone):
            return f"current_zone_blocked zone={curr_zone} current_tile={curr_tile}"

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
                f"[OBS] confirming obstacle... {elapsed:.1f}/{self.confirm_blocked_sec:.1f}s reason={reason}",
                throttle_duration_sec=0.7,
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
            f"lidar={self.lidar_state.occupied if self.lidar_state.online else 'off'} "
            f"dist={dist:.3f} progress={progress:.3f}/{seg_len:.3f} state={self.motion_state}",
            throttle_duration_sec=1.0,
        )

        # Si ya ha llegado al tile objetivo, avanza en la ruta
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

        # ---------------- ROTATE ----------------
        if self.motion_state == "ROTATE_TO_EDGE":
            if self.latched_target_yaw is None:
                self.latched_target_yaw = self.heading_for_edge(prev_tile, target_tile)
                self.get_logger().info(
                    f"[ROT] edge={prev_tile}->{target_tile} "
                    f"target_yaw={math.degrees(self.latched_target_yaw):.1f} deg"
                )

            yaw_error = normalize_angle(self.latched_target_yaw - self.current_pose.yaw)

            # Solo un epsilon mínimo para saber cuándo parar
            if abs(yaw_error) <= self.stop_yaw_epsilon:
                self.publish_zero()
                self.motion_state = "MOVE_TO_EDGE"
                self.get_logger().info("[ROT] completed. Switching to MOVE.")
                return

            cmd = Twist()
            cmd.linear.x = 0.0

            # Giro simple por signo, con frenado leve cerca del objetivo
            if abs(yaw_error) > math.radians(20.0):
                cmd.angular.z = self.angular_speed if yaw_error > 0.0 else -self.angular_speed
            elif abs(yaw_error) > math.radians(8.0):
                slow = 0.45 * self.angular_speed
                cmd.angular.z = slow if yaw_error > 0.0 else -slow
            else:
                very_slow = 0.22 * self.angular_speed
                cmd.angular.z = very_slow if yaw_error > 0.0 else -very_slow

            self.cmd_pub.publish(cmd)
            return

        # ---------------- MOVE ----------------
        if self.motion_state == "MOVE_TO_EDGE":
            target_yaw = self.heading_for_edge(prev_tile, target_tile)
            yaw_error = normalize_angle(target_yaw - self.current_pose.yaw)

            cmd = Twist()
            cmd.linear.x = self.linear_speed

            correction = max(
                -self.max_angular_correction,
                min(self.max_angular_correction, 0.8 * yaw_error)
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
