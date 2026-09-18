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
# MAP CONFIG
# ============================================================

ROWS = 10
COLS = 8
DEFAULT_TILE_SIZE_M = 0.62

START_TILE = (0, 6)
GOAL_TILE = (6, 4)

STATIC_WALLS: Set[Tuple[int, int]] = {
    (0, 0), (1, 0), (2, 0), (3, 0), (6, 0), (7, 0),
    (0, 1), (1, 1), (2, 1), (3, 1), (6, 1), (4, 3), (5, 3), (5, 5), (4, 6),
    (0, 2), (1, 2),
    (0, 3), (1, 3),
    (0, 4), (1, 4),
    (3, 4), (4, 4), (5, 4),
    (3, 5), (4, 5),
    (3, 6),
    (7, 1), (7, 2), (7, 3), (7, 4), (7, 5), (7, 6), (7, 7), (7, 8),
    (3, 9), (4, 9), (5, 9), (6, 9), (7, 9),
}

HIDDEN_OBSTACLE_TILES: Set[Tuple[int, int]] = {
    (6, 5),
}

OBSTACLE_LABELS = {"turtlebot", "person_1"}


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


def manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def neighbors(tile: Tuple[int, int], blocked: Set[Tuple[int, int]]) -> List[Tuple[int, int]]:
    x, y = tile
    candidates = [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
    return [t for t in candidates if inside(t) and t not in blocked]


def reconstruct_path(
    came_from: Dict[Tuple[int, int], Tuple[int, int]],
    current: Tuple[int, int],
) -> List[Tuple[int, int]]:
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path


def a_star(
    start: Tuple[int, int],
    goal: Tuple[int, int],
    blocked: Set[Tuple[int, int]],
) -> Optional[List[Tuple[int, int]]]:
    if start in blocked or goal in blocked:
        return None

    open_list: List[Tuple[int, int]] = [start]
    open_set = {start}
    came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
    g_score: Dict[Tuple[int, int], float] = {start: 0.0}
    f_score: Dict[Tuple[int, int], float] = {start: float(manhattan(start, goal))}

    while open_list:
        open_list.sort(key=lambda t: f_score.get(t, float("inf")))
        current = open_list.pop(0)
        open_set.remove(current)

        if current == goal:
            return reconstruct_path(came_from, current)

        for nb in neighbors(current, blocked):
            tentative = g_score[current] + 1.0
            if tentative < g_score.get(nb, float("inf")):
                came_from[nb] = current
                g_score[nb] = tentative
                f_score[nb] = tentative + float(manhattan(nb, goal))
                if nb not in open_set:
                    open_list.append(nb)
                    open_set.add(nb)

    return None


@dataclass
class Pose2D:
    x: float
    y: float
    yaw: float


# ============================================================
# NODE
# ============================================================

class GridRouteWalkNode(Node):
    def __init__(self):
        super().__init__("grid_route_walk")

        self.declare_parameter("cmd_topic", "/cmd_vel_out")
        self.declare_parameter("odom_topic", "/odom")
        self.declare_parameter(
            "csi_state_json",
            "/home/nextnet/AlbertoDir/go2_dt/csi_dog_dataset_20210421_181125/analysis_outputs/live_prediction_state.json",
        )
        self.declare_parameter("loop_hz", 20.0)
        self.declare_parameter("csi_poll_hz", 5.0)
        self.declare_parameter("linear_speed", 0.10)
        self.declare_parameter("angular_speed", 0.35)
        self.declare_parameter("tile_size", DEFAULT_TILE_SIZE_M)

        self.declare_parameter("waypoint_pos_tolerance", 0.10)
        self.declare_parameter("rotate_tolerance_deg", 6.0)
        self.declare_parameter("move_yaw_gate_deg", 14.0)
        self.declare_parameter("max_angular_correction", 0.18)

        self.declare_parameter("confirm_obstacle_sec", 3.0)
        self.declare_parameter("stale_csi_sec", 2.5)

        # NUEVO: criterio robusto de fin de segmento
        self.declare_parameter("segment_completion_margin", 0.08)

        self.cmd_topic = self.get_parameter("cmd_topic").get_parameter_value().string_value
        self.odom_topic = self.get_parameter("odom_topic").get_parameter_value().string_value
        self.csi_state_json = self.get_parameter("csi_state_json").get_parameter_value().string_value
        self.loop_hz = self.get_parameter("loop_hz").get_parameter_value().double_value
        self.csi_poll_hz = self.get_parameter("csi_poll_hz").get_parameter_value().double_value
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
        self.confirm_obstacle_sec = self.get_parameter("confirm_obstacle_sec").get_parameter_value().double_value
        self.stale_csi_sec = self.get_parameter("stale_csi_sec").get_parameter_value().double_value
        self.segment_completion_margin = self.get_parameter("segment_completion_margin").get_parameter_value().double_value

        self.cmd_pub = self.create_publisher(Twist, self.cmd_topic, 10)
        self.odom_sub = self.create_subscription(Odometry, self.odom_topic, self.odom_callback, 20)
        self.control_timer = self.create_timer(1.0 / self.csi_poll_hz, self.poll_csi_state)
        self.loop_timer = self.create_timer(1.0 / self.loop_hz, self.control_loop)

        self.current_pose: Optional[Pose2D] = None
        self.anchor_pose: Optional[Pose2D] = None

        self.forward_vec: Optional[Tuple[float, float]] = None
        self.right_vec: Optional[Tuple[float, float]] = None

        self.current_label: str = "unknown"
        self.current_label_wall_time: Optional[float] = None

        self.waiting_for_confirmation = False
        self.obstacle_candidate_since: Optional[float] = None
        self.replan_committed = False

        self.returning_home = False
        self.mission_complete = False

        self.nominal_forward_path = self.compute_nominal_forward_path()
        self.fixed_return_path = list(reversed(self.nominal_forward_path))

        self.active_path: List[Tuple[int, int]] = list(self.nominal_forward_path)
        self.path_name = "nominal_forward"
        self.current_waypoint_index = 1

        self.motion_state = "ROTATE_TO_EDGE"
        self.latched_target_yaw: Optional[float] = None

        self.get_logger().info(f"Grid route walker started. cmd_topic={self.cmd_topic}, odom_topic={self.odom_topic}")
        self.get_logger().info(
            f"Start tile={START_TILE}, goal tile={GOAL_TILE}, tile_size={self.tile_size:.3f} m"
        )
        self.get_logger().info(f"Nominal forward path: {self.nominal_forward_path}")
        self.get_logger().info(f"Fixed return path: {self.fixed_return_path}")

    # --------------------------------------------------------
    # Planning
    # --------------------------------------------------------

    def compute_nominal_forward_path(self) -> List[Tuple[int, int]]:
        path = a_star(START_TILE, GOAL_TILE, STATIC_WALLS)
        if path is None:
            raise RuntimeError("No nominal path exists from START_TILE to GOAL_TILE.")
        return path

    def compute_replanned_path(self, start_tile: Tuple[int, int]) -> Optional[List[Tuple[int, int]]]:
        blocked = set(STATIC_WALLS)
        blocked.update(HIDDEN_OBSTACLE_TILES)
        return a_star(start_tile, GOAL_TILE, blocked)

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
    # CSI logic
    # --------------------------------------------------------

    def read_csi_state(self) -> Tuple[str, Optional[float]]:
        if not os.path.exists(self.csi_state_json):
            return "unknown", None

        try:
            mtime = os.path.getmtime(self.csi_state_json)
            with open(self.csi_state_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            label = str(data.get("prediction", "unknown")).strip().lower()
            if not label:
                label = "unknown"
            return label, mtime
        except Exception as e:
            self.get_logger().warn(f"Could not read CSI JSON: {e}")
            return "unknown", None

    def poll_csi_state(self):
        label, mtime = self.read_csi_state()
        self.current_label = label
        self.current_label_wall_time = mtime

    def obstacle_detected_now(self) -> bool:
        if self.current_label_wall_time is None:
            return False
        age = max(0.0, time.time() - self.current_label_wall_time)
        if age > self.stale_csi_sec:
            return False
        return self.current_label in OBSTACLE_LABELS

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
        """
        Devuelve:
        - progress: proyección del robot sobre el segmento prev->target
        - seg_len: longitud total del segmento
        """
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
        """
        Criterio robusto:
        1) cerca del centro,
        2) snapped tile == target tile,
        3) progreso suficiente a lo largo del segmento.
        """
        if dist_to_target <= self.waypoint_pos_tolerance:
            return True

        snapped = self.current_snapped_tile()
        if snapped == target_tile:
            return True

        progress, seg_len = self.segment_progress_and_length(prev_tile, target_tile)

        # Si ya ha recorrido prácticamente todo el segmento, aunque no haya pasado por el centro exacto.
        if seg_len > 1e-9 and progress >= (seg_len - self.segment_completion_margin):
            return True

        # Si ya ha sobrepasado el objetivo claramente
        if seg_len > 1e-9 and progress >= seg_len:
            return True

        return False

    def advance_waypoint(self):
        self.current_waypoint_index += 1
        self.motion_state = "ROTATE_TO_EDGE"
        self.latched_target_yaw = None

    def set_active_path(self, new_path: List[Tuple[int, int]], path_name: str):
        self.active_path = list(new_path)
        self.path_name = path_name
        self.current_waypoint_index = 1
        self.motion_state = "ROTATE_TO_EDGE"
        self.latched_target_yaw = None
        self.get_logger().info(f"Active path changed to '{path_name}': {new_path}")

    def reached_final_tile(self) -> bool:
        return self.current_waypoint_index >= len(self.active_path)

    # --------------------------------------------------------
    # Mission logic
    # --------------------------------------------------------

    def maybe_start_confirmation_stop(self):
        if self.returning_home or self.replan_committed or self.mission_complete:
            return

        if self.waiting_for_confirmation:
            return

        if self.obstacle_detected_now():
            self.waiting_for_confirmation = True
            self.obstacle_candidate_since = time.time()
            self.publish_zero()
            self.get_logger().warn(
                f"Obstacle candidate detected (label={self.current_label}). Stopping for confirmation."
            )

    def process_confirmation_logic(self) -> bool:
        if not self.waiting_for_confirmation:
            return False

        self.publish_zero()

        if not self.obstacle_detected_now():
            self.waiting_for_confirmation = False
            self.obstacle_candidate_since = None
            self.get_logger().info("Obstacle disappeared before confirmation. Resuming current path.")
            return False

        elapsed = time.time() - (self.obstacle_candidate_since or time.time())

        if elapsed < self.confirm_obstacle_sec:
            self.get_logger().info(
                f"[OBS] confirming obstacle... {elapsed:.1f}/{self.confirm_obstacle_sec:.1f}s",
                throttle_duration_sec=0.7,
            )
            return True

        snapped_tile = self.current_snapped_tile()
        if snapped_tile is None:
            self.get_logger().error("Cannot replan: current tile is outside map. Staying stopped.")
            return True

        replanned = self.compute_replanned_path(snapped_tile)
        if replanned is None:
            self.get_logger().error(f"No replanned path exists from tile {snapped_tile}. Staying stopped.")
            return True

        self.replan_committed = True
        self.waiting_for_confirmation = False
        self.obstacle_candidate_since = None
        self.set_active_path(replanned, "rerouted_forward")
        self.get_logger().warn(
            f"Obstacle confirmed after {self.confirm_obstacle_sec:.1f}s. "
            f"Current tile={snapped_tile}. Going by alternative route."
        )
        return True

    def handle_goal_and_return(self) -> bool:
        if not self.reached_final_tile():
            return False

        self.publish_zero()

        if not self.returning_home:
            self.returning_home = True
            self.set_active_path(self.fixed_return_path, "fixed_return")
            self.get_logger().info("Goal reached. Starting fixed return path to initial tile.")
            return True

        self.mission_complete = True
        self.get_logger().info("Return path completed. Mission finished.")
        return True

    # --------------------------------------------------------
    # Main controller
    # --------------------------------------------------------

    def control_loop(self):
        if self.current_pose is None or self.anchor_pose is None:
            self.get_logger().info("Waiting for odometry anchor...", throttle_duration_sec=2.0)
            return

        if self.mission_complete:
            self.publish_zero()
            return

        self.maybe_start_confirmation_stop()

        if self.process_confirmation_logic():
            return

        if self.handle_goal_and_return():
            return

        if self.current_waypoint_index >= len(self.active_path):
            self.publish_zero()
            return

        prev_tile = self.active_path[self.current_waypoint_index - 1]
        target_tile = self.active_path[self.current_waypoint_index]

        wx, wy = self.tile_to_world(target_tile)
        dist = self.distance_to_world_point(wx, wy)
        snapped = self.current_snapped_tile()
        progress, seg_len = self.segment_progress_and_length(prev_tile, target_tile)

        self.get_logger().info(
            f"[CTRL] path={self.path_name} prev_tile={prev_tile} target_tile={target_tile} "
            f"world=({wx:.2f},{wy:.2f}) pos=({self.current_pose.x:.2f},{self.current_pose.y:.2f}) "
            f"dist={dist:.3f} snapped={snapped} progress={progress:.3f}/{seg_len:.3f} "
            f"state={self.motion_state}",
            throttle_duration_sec=1.0,
        )

        if self.waypoint_reached(prev_tile, target_tile, dist):
            self.get_logger().info(
                f"Reached waypoint tile={target_tile}, dist={dist:.3f} m, "
                f"snapped={snapped}, progress={progress:.3f}/{seg_len:.3f}. Advancing to next."
            )
            self.advance_waypoint()
            self.publish_zero()
            return

        if self.motion_state == "ROTATE_TO_EDGE":
            if self.latched_target_yaw is None:
                self.latched_target_yaw = self.heading_for_edge(prev_tile, target_tile)
                self.get_logger().info(
                    f"[ROT] New exact grid heading for edge {prev_tile}->{target_tile}: "
                    f"{math.degrees(self.latched_target_yaw):.1f} deg"
                )

            yaw_error = normalize_angle(self.latched_target_yaw - self.current_pose.yaw)

            self.get_logger().info(
                f"[ROT] edge={prev_tile}->{target_tile} "
                f"target_yaw={math.degrees(self.latched_target_yaw):.1f} deg "
                f"current_yaw={math.degrees(self.current_pose.yaw):.1f} deg "
                f"error={math.degrees(yaw_error):.1f} deg",
                throttle_duration_sec=0.5,
            )

            if abs(yaw_error) <= self.rotate_tolerance:
                self.motion_state = "MOVE_TO_EDGE"
                self.publish_zero()
                self.get_logger().info(f"[ROT] Heading aligned for edge {prev_tile}->{target_tile}. Switching to MOVE.")
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
                    f"[MOVE] Large yaw drift detected ({math.degrees(yaw_error):.1f} deg). Re-rotating."
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
    node = GridRouteWalkNode()
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