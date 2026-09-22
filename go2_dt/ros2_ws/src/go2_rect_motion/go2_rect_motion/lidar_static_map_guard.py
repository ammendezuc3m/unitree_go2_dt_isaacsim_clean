#!/usr/bin/env python3
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import rclpy
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time

from nav_msgs.msg import Odometry
from sensor_msgs.msg import LaserScan, PointCloud2
from sensor_msgs_py import point_cloud2

from tf2_ros import Buffer, TransformListener
from tf2_ros import ConnectivityException, ExtrapolationException, LookupException
from tf2_sensor_msgs.tf2_sensor_msgs import do_transform_cloud

try:
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except Exception:
    plt = None
    MATPLOTLIB_AVAILABLE = False


ROS_WS_ROOT = Path(__file__).resolve().parents[3]
LIDAR_OUTPUT_DIR = ROS_WS_ROOT / "lidar_outputs"

DEFAULT_OUTPUT_JSON = str(LIDAR_OUTPUT_DIR / "live_lidar_state.json")
DEFAULT_WORLD_BASELINE_JSON = str(LIDAR_OUTPUT_DIR / "lidar_baseline_world.json")
DEFAULT_DEBUG_IMAGE = str(LIDAR_OUTPUT_DIR / "live_lidar_guard_debug.png")


@dataclass
class Pose2D:
    x: float
    y: float
    yaw: float


@dataclass
class NovelCluster:
    id: int
    point_count: int
    cell_count: int
    centroid_x: float
    centroid_y: float
    inside_ratio: float
    detected: bool

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "point_count": self.point_count,
            "cell_count": self.cell_count,
            "centroid": [round(self.centroid_x, 3), round(self.centroid_y, 3)],
            "inside_ratio": round(self.inside_ratio, 3),
            "detected": self.detected,
        }


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def point_in_polygon(x: float, y: float, polygon: List[Tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return False

    inside = False
    j = len(polygon) - 1

    for i in range(len(polygon)):
        xi, yi = polygon[i]
        xj, yj = polygon[j]

        if (yi > y) != (yj > y):
            x_intersect = (xj - xi) * (y - yi) / ((yj - yi) + 1e-12) + xi
            if x < x_intersect:
                inside = not inside

        j = i

    return inside


def build_rotated_box(
    cx: float,
    cy: float,
    width: float,
    height: float,
    rot_deg: float,
) -> List[Tuple[float, float]]:
    theta = math.radians(rot_deg)
    c = math.cos(theta)
    s = math.sin(theta)

    hw = width / 2.0
    hh = height / 2.0

    corners = [
        (-hw, -hh),
        (-hw, hh),
        (hw, hh),
        (hw, -hh),
    ]

    out: List[Tuple[float, float]] = []
    for x, y in corners:
        out.append((cx + c * x - s * y, cy + s * x + c * y))

    return out


class LidarStaticMapGuard(Node):
    def __init__(self):
        super().__init__("lidar_static_map_guard")

        self.declare_parameter("odom_topic", "/odom")
        self.declare_parameter("cloud_topic", "/point_cloud2")
        self.declare_parameter("scan_topic", "/scan")
        self.declare_parameter("input_source", "hybrid")

        self.declare_parameter("output_json", DEFAULT_OUTPUT_JSON)
        self.declare_parameter("world_baseline_json", DEFAULT_WORLD_BASELINE_JSON)

        self.declare_parameter("target_frame", "map")
        self.declare_parameter("robot_base_frame", "base_link")
        self.declare_parameter("tf_timeout_sec", 0.2)

        self.declare_parameter("learn_mode", False)
        self.declare_parameter("learning_frames", 900)
        self.declare_parameter("learning_warmup_frames", 20)

        self.declare_parameter("cell_size", 0.08)

        self.declare_parameter("z_min", 0.0)
        self.declare_parameter("z_max", 1.4)
        self.declare_parameter("min_range", 0.2)
        self.declare_parameter("max_range", 4.0)
        self.declare_parameter("point_stride", 1)
        self.declare_parameter("scan_point_weight", 4)

        self.declare_parameter("interest_center_x", 1.10)
        self.declare_parameter("interest_center_y", -1.28)
        self.declare_parameter("interest_width_m", 3.15)
        self.declare_parameter("interest_height_m", 2.80)
        self.declare_parameter("interest_rotation_deg", 0.0)
        self.declare_parameter("interest_inset_m", 0.30)

        self.declare_parameter("baseline_presence_threshold", 4.0)
        self.declare_parameter("baseline_exclusion_threshold", 1.0)
        self.declare_parameter("baseline_exclusion_radius_cells", 2)
        self.declare_parameter("min_distance_from_baseline_cells", 3)

        self.declare_parameter("novel_compare_radius_cells", 1)
        self.declare_parameter("novel_local_min_points", 8.0)
        self.declare_parameter("novel_cell_min_points", 5)

        self.declare_parameter("cluster_min_points", 18)
        self.declare_parameter("cluster_min_cells", 4)
        self.declare_parameter("cluster_inside_ratio_threshold", 0.90)

        self.declare_parameter("confirm_frames", 2)
        self.declare_parameter("clear_frames", 2)

        self.declare_parameter("stale_cloud_sec", 1.0)
        self.declare_parameter("stale_scan_sec", 1.0)
        self.declare_parameter("stale_odom_sec", 1.0)
        self.declare_parameter("poll_hz", 10.0)

        self.declare_parameter("show_popup", True)
        self.declare_parameter("popup_stride", 2)
        self.declare_parameter("save_debug_image", False)
        self.declare_parameter("debug_image_stride", 10)
        self.declare_parameter("debug_image_path", DEFAULT_DEBUG_IMAGE)

        # Auto-alignment. No hay modo manual.
        self.declare_parameter("alignment_every_n_frames", 2)
        self.declare_parameter("alignment_min_points", 60)
        self.declare_parameter("alignment_max_points", 900)

        self.declare_parameter("alignment_coarse_angle_window_deg", 25.0)
        self.declare_parameter("alignment_coarse_angle_step_deg", 2.0)
        self.declare_parameter("alignment_coarse_translation_window_m", 0.60)
        self.declare_parameter("alignment_coarse_translation_step_m", 0.10)

        self.declare_parameter("alignment_fine_angle_window_deg", 5.0)
        self.declare_parameter("alignment_fine_angle_step_deg", 0.5)
        self.declare_parameter("alignment_fine_translation_window_m", 0.15)
        self.declare_parameter("alignment_fine_translation_step_m", 0.025)

        self.declare_parameter("alignment_accept_min_score", 0.18)
        self.declare_parameter("alignment_smoothing_alpha", 0.35)
        self.declare_parameter("alignment_use_outside_roi", True)

        self.odom_topic = self.get_parameter("odom_topic").value
        self.cloud_topic = self.get_parameter("cloud_topic").value
        self.scan_topic = self.get_parameter("scan_topic").value
        self.input_source = str(self.get_parameter("input_source").value).lower().strip()

        self.output_json = self.get_parameter("output_json").value
        self.world_baseline_json = self.get_parameter("world_baseline_json").value

        self.target_frame = self.get_parameter("target_frame").value
        self.robot_base_frame = self.get_parameter("robot_base_frame").value
        self.tf_timeout_sec = float(self.get_parameter("tf_timeout_sec").value)

        self.learn_mode = bool(self.get_parameter("learn_mode").value)
        self.learning_frames = int(self.get_parameter("learning_frames").value)
        self.learning_warmup_frames = int(self.get_parameter("learning_warmup_frames").value)

        self.world_cell_size = float(self.get_parameter("cell_size").value)

        self.z_min = float(self.get_parameter("z_min").value)
        self.z_max = float(self.get_parameter("z_max").value)
        self.min_range = float(self.get_parameter("min_range").value)
        self.max_range = float(self.get_parameter("max_range").value)
        self.point_stride = max(1, int(self.get_parameter("point_stride").value))
        self.scan_point_weight = max(1, int(self.get_parameter("scan_point_weight").value))

        self.baseline_presence_threshold = float(self.get_parameter("baseline_presence_threshold").value)
        self.baseline_exclusion_threshold = float(self.get_parameter("baseline_exclusion_threshold").value)
        self.baseline_exclusion_radius_cells = int(self.get_parameter("baseline_exclusion_radius_cells").value)
        self.min_distance_from_baseline_cells = int(self.get_parameter("min_distance_from_baseline_cells").value)

        self.novel_compare_radius_cells = int(self.get_parameter("novel_compare_radius_cells").value)
        self.novel_local_min_points = float(self.get_parameter("novel_local_min_points").value)
        self.novel_cell_min_points = int(self.get_parameter("novel_cell_min_points").value)

        self.cluster_min_points = int(self.get_parameter("cluster_min_points").value)
        self.cluster_min_cells = int(self.get_parameter("cluster_min_cells").value)
        self.cluster_inside_ratio_threshold = float(self.get_parameter("cluster_inside_ratio_threshold").value)

        self.confirm_frames = int(self.get_parameter("confirm_frames").value)
        self.clear_frames = int(self.get_parameter("clear_frames").value)

        self.stale_cloud_sec = float(self.get_parameter("stale_cloud_sec").value)
        self.stale_scan_sec = float(self.get_parameter("stale_scan_sec").value)
        self.stale_odom_sec = float(self.get_parameter("stale_odom_sec").value)
        self.poll_hz = float(self.get_parameter("poll_hz").value)

        self.show_popup = bool(self.get_parameter("show_popup").value)
        self.popup_stride = max(1, int(self.get_parameter("popup_stride").value))
        self.save_debug_image = bool(self.get_parameter("save_debug_image").value)
        self.debug_image_stride = max(1, int(self.get_parameter("debug_image_stride").value))
        self.debug_image_path = self.get_parameter("debug_image_path").value

        self.alignment_every_n_frames = max(1, int(self.get_parameter("alignment_every_n_frames").value))
        self.alignment_min_points = int(self.get_parameter("alignment_min_points").value)
        self.alignment_max_points = int(self.get_parameter("alignment_max_points").value)

        self.alignment_coarse_angle_window_deg = float(self.get_parameter("alignment_coarse_angle_window_deg").value)
        self.alignment_coarse_angle_step_deg = float(self.get_parameter("alignment_coarse_angle_step_deg").value)
        self.alignment_coarse_translation_window_m = float(self.get_parameter("alignment_coarse_translation_window_m").value)
        self.alignment_coarse_translation_step_m = float(self.get_parameter("alignment_coarse_translation_step_m").value)

        self.alignment_fine_angle_window_deg = float(self.get_parameter("alignment_fine_angle_window_deg").value)
        self.alignment_fine_angle_step_deg = float(self.get_parameter("alignment_fine_angle_step_deg").value)
        self.alignment_fine_translation_window_m = float(self.get_parameter("alignment_fine_translation_window_m").value)
        self.alignment_fine_translation_step_m = float(self.get_parameter("alignment_fine_translation_step_m").value)

        self.alignment_accept_min_score = float(self.get_parameter("alignment_accept_min_score").value)
        self.alignment_smoothing_alpha = float(self.get_parameter("alignment_smoothing_alpha").value)
        self.alignment_use_outside_roi = bool(self.get_parameter("alignment_use_outside_roi").value)

        cx = float(self.get_parameter("interest_center_x").value)
        cy = float(self.get_parameter("interest_center_y").value)
        w = float(self.get_parameter("interest_width_m").value)
        h = float(self.get_parameter("interest_height_m").value)
        r = float(self.get_parameter("interest_rotation_deg").value)
        inset = float(self.get_parameter("interest_inset_m").value)

        self.interest_polygon = build_rotated_box(cx, cy, w, h, r)
        self.interest_polygon_inner = build_rotated_box(
            cx,
            cy,
            max(0.20, w - 2.0 * inset),
            max(0.20, h - 2.0 * inset),
            r,
        )

        self.current_pose: Optional[Pose2D] = None
        self.latest_cloud_msg: Optional[PointCloud2] = None
        self.latest_scan_msg: Optional[LaserScan] = None

        self.last_cloud_wall_time: Optional[float] = None
        self.last_scan_wall_time: Optional[float] = None
        self.last_odom_wall_time: Optional[float] = None

        self.current_input_source = self.input_source

        self.world_baseline_mean_counts: Dict[str, float] = {}
        self.learning_sums: Dict[str, int] = {}
        self.learning_count = 0
        self.learning_warmup_count = 0
        self.learning_done = False

        self.current_points_with_z: List[Tuple[float, float, float]] = []
        self.current_cloud_points_with_z: List[Tuple[float, float, float]] = []
        self.current_scan_points_with_z: List[Tuple[float, float, float]] = []
        self.current_novel_cells_for_plot: Dict[str, float] = {}

        self.alignment_ready = False
        self.alignment_rotation_rad = 0.0
        self.alignment_tx = 0.0
        self.alignment_ty = 0.0
        self.last_alignment_score = 0.0

        self.confirm_counter = 0
        self.clear_counter = 0
        self.latched_detected = False
        self.latest_clusters: List[NovelCluster] = []

        self.loop_counter = 0
        self.popup_update_counter = 0
        self.last_processed_stamp_key: Optional[str] = None

        self.figure = None
        self.ax_baseline = None
        self.ax_current = None
        self.ax_novel = None

        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )

        self.create_subscription(Odometry, self.odom_topic, self.odom_callback, 20)
        self.create_subscription(PointCloud2, self.cloud_topic, self.cloud_callback, qos)
        self.create_subscription(LaserScan, self.scan_topic, self.scan_callback, qos)

        self.create_timer(1.0 / self.poll_hz, self.on_timer)

        self.load_baseline()

        if self.learn_mode:
            self.world_baseline_mean_counts = {}

        self.setup_popup()

        self.get_logger().info(
            f"LiDAR guard started | learn_mode={self.learn_mode} | "
            f"input_source={self.input_source} | baseline={self.world_baseline_json}"
        )

    def odom_callback(self, msg: Odometry):
        q = msg.pose.pose.orientation
        self.current_pose = Pose2D(
            float(msg.pose.pose.position.x),
            float(msg.pose.pose.position.y),
            yaw_from_quaternion(q.x, q.y, q.z, q.w),
        )
        self.last_odom_wall_time = time.time()

    def cloud_callback(self, msg: PointCloud2):
        self.latest_cloud_msg = msg
        self.last_cloud_wall_time = time.time()
        self.get_logger().info(
            f"Cloud received | width={msg.width} | frame={msg.header.frame_id}",
            throttle_duration_sec=2.0,
        )

    def scan_callback(self, msg: LaserScan):
        self.latest_scan_msg = msg
        self.last_scan_wall_time = time.time()
        self.get_logger().info(
            f"Scan received | points={len(msg.ranges)} | frame={msg.header.frame_id}",
            throttle_duration_sec=2.0,
        )

    def lookup_transform(self, target_frame, source_frame, stamp):
        try:
            return self.tf_buffer.lookup_transform(
                target_frame,
                source_frame,
                Time.from_msg(stamp),
                timeout=Duration(seconds=self.tf_timeout_sec),
            )
        except Exception:
            try:
                return self.tf_buffer.lookup_transform(
                    target_frame,
                    source_frame,
                    Time(),
                    timeout=Duration(seconds=self.tf_timeout_sec),
                )
            except (LookupException, ConnectivityException, ExtrapolationException) as e:
                self.get_logger().warn(
                    f"TF unavailable {source_frame}->{target_frame}: {e}",
                    throttle_duration_sec=2.0,
                )
                return None

    def update_robot_pose_from_tf(self) -> bool:
        tf = self.lookup_transform(
            self.target_frame,
            self.robot_base_frame,
            Time().to_msg(),
        )

        if tf is None:
            return False

        t = tf.transform.translation
        q = tf.transform.rotation

        self.current_pose = Pose2D(
            float(t.x),
            float(t.y),
            yaw_from_quaternion(q.x, q.y, q.z, q.w),
        )
        self.last_odom_wall_time = time.time()
        return True

    def stamp_ns(self, stamp):
        return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)

    def active_input_source(self) -> Optional[str]:
        now = time.time()

        cloud_ok = (
            self.latest_cloud_msg is not None
            and self.last_cloud_wall_time is not None
            and now - self.last_cloud_wall_time <= self.stale_cloud_sec
        )

        scan_ok = (
            self.latest_scan_msg is not None
            and self.last_scan_wall_time is not None
            and now - self.last_scan_wall_time <= self.stale_scan_sec
        )

        if self.learn_mode:
            return "pointcloud" if cloud_ok else None

        if self.input_source == "pointcloud":
            return "pointcloud" if cloud_ok else None

        if self.input_source == "scan":
            return "scan" if scan_ok else None

        if cloud_ok and scan_ok:
            return "hybrid"

        if cloud_ok:
            return "pointcloud"

        if scan_ok:
            return "scan"

        return None

    def current_stamp_key(self, source):
        if source == "hybrid":
            pc = self.stamp_ns(self.latest_cloud_msg.header.stamp) if self.latest_cloud_msg else 0
            sc = self.stamp_ns(self.latest_scan_msg.header.stamp) if self.latest_scan_msg else 0
            return f"hybrid:{pc}:{sc}"

        if source == "pointcloud" and self.latest_cloud_msg:
            return f"pointcloud:{self.stamp_ns(self.latest_cloud_msg.header.stamp)}"

        if source == "scan" and self.latest_scan_msg:
            return f"scan:{self.stamp_ns(self.latest_scan_msg.header.stamp)}"

        return None

    def cell_key(self, x, y):
        return f"{math.floor(x / self.world_cell_size)},{math.floor(y / self.world_cell_size)}"

    def cell_index(self, x, y):
        return math.floor(x / self.world_cell_size), math.floor(y / self.world_cell_size)

    def local_sum(self, counts: Dict[str, float], ix: int, iy: int, radius: int) -> float:
        total = 0.0

        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                total += float(counts.get(f"{ix + dx},{iy + dy}", 0.0))

        return total

    def point_in_roi(self, x, y):
        return point_in_polygon(x, y, self.interest_polygon_inner)

    def polygon_closed(self):
        if not self.interest_polygon:
            return []
        return self.interest_polygon + [self.interest_polygon[0]]

    def rotate_vector_by_quaternion(self, vx, vy, vz, qx, qy, qz, qw):
        tx = 2.0 * (qy * vz - qz * vy)
        ty = 2.0 * (qz * vx - qx * vz)
        tz = 2.0 * (qx * vy - qy * vx)

        rx = vx + qw * tx + (qy * tz - qz * ty)
        ry = vy + qw * ty + (qz * tx - qx * tz)
        rz = vz + qw * tz + (qx * ty - qy * tx)

        return rx, ry, rz

    def align_current_to_baseline(self, x, y):
        c = math.cos(self.alignment_rotation_rad)
        s = math.sin(self.alignment_rotation_rad)

        return (
            c * x - s * y + self.alignment_tx,
            s * x + c * y + self.alignment_ty,
        )

    def extract_from_cloud(self):
        points = []

        if self.latest_cloud_msg is None or self.current_pose is None:
            return points

        tf = self.lookup_transform(
            self.target_frame,
            self.latest_cloud_msg.header.frame_id,
            self.latest_cloud_msg.header.stamp,
        )

        if tf is None:
            return points

        try:
            cloud = do_transform_cloud(self.latest_cloud_msg, tf)
        except Exception as e:
            self.get_logger().warn(f"Could not transform pointcloud: {e}")
            return points

        idx = 0
        for x, y, z in point_cloud2.read_points(
            cloud,
            field_names=("x", "y", "z"),
            skip_nans=True,
        ):
            idx += 1

            if self.point_stride > 1 and idx % self.point_stride != 0:
                continue

            x = float(x)
            y = float(y)
            z = float(z)

            if z < self.z_min or z > self.z_max:
                continue

            dist = math.hypot(x - self.current_pose.x, y - self.current_pose.y)

            if dist < self.min_range or dist > self.max_range:
                continue

            points.append((x, y, z))

        return points

    def extract_from_scan(self):
        points = []

        if self.latest_scan_msg is None or self.current_pose is None:
            return points

        msg = self.latest_scan_msg
        use_direct_pose = msg.header.frame_id == self.robot_base_frame

        tf = None
        if not use_direct_pose:
            tf = self.lookup_transform(
                self.target_frame,
                msg.header.frame_id,
                msg.header.stamp,
            )

            if tf is None:
                return points

        angle = msg.angle_min
        range_max = msg.range_max if msg.range_max > 0.0 else self.max_range

        for idx, distance in enumerate(msg.ranges):
            if self.point_stride > 1 and idx % self.point_stride != 0:
                angle += msg.angle_increment
                continue

            if not math.isfinite(distance):
                angle += msg.angle_increment
                continue

            if distance < max(self.min_range, msg.range_min):
                angle += msg.angle_increment
                continue

            if distance > min(self.max_range, range_max):
                angle += msg.angle_increment
                continue

            sx = distance * math.cos(angle)
            sy = distance * math.sin(angle)
            sz = 0.0
            angle += msg.angle_increment

            if use_direct_pose:
                c = math.cos(self.current_pose.yaw)
                s = math.sin(self.current_pose.yaw)

                x = self.current_pose.x + c * sx - s * sy
                y = self.current_pose.y + s * sx + c * sy
                z = 0.0
            else:
                t = tf.transform.translation
                q = tf.transform.rotation

                rx, ry, rz = self.rotate_vector_by_quaternion(
                    sx,
                    sy,
                    sz,
                    q.x,
                    q.y,
                    q.z,
                    q.w,
                )

                x = float(t.x + rx)
                y = float(t.y + ry)
                z = float(t.z + rz)

            if z < self.z_min or z > self.z_max:
                continue

            dist = math.hypot(x - self.current_pose.x, y - self.current_pose.y)

            if dist < self.min_range or dist > self.max_range:
                continue

            for _ in range(self.scan_point_weight):
                points.append((x, y, z))

        return points

    def extract_current_points(self):
        cloud_points = []
        scan_points = []

        if self.current_input_source in ("pointcloud", "hybrid"):
            cloud_points = self.extract_from_cloud()

        if self.current_input_source in ("scan", "hybrid"):
            scan_points = self.extract_from_scan()

        self.current_cloud_points_with_z = cloud_points
        self.current_scan_points_with_z = scan_points
        self.current_points_with_z = cloud_points + scan_points

    def points_to_counts(self, points):
        counts: Dict[str, int] = {}

        for x, y, _z in points:
            key = self.cell_key(x, y)
            counts[key] = counts.get(key, 0) + 1

        return counts

    def load_baseline(self):
        if not os.path.exists(self.world_baseline_json):
            self.get_logger().warn(f"Baseline not found: {self.world_baseline_json}")
            return

        try:
            with open(self.world_baseline_json, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.world_cell_size = float(data.get("cell_size", self.world_cell_size))

            raw = data.get("baseline_mean_counts", {})
            if not raw:
                raw = data.get("baseline_counts", {})

            self.world_baseline_mean_counts = {
                str(k): float(v)
                for k, v in raw.items()
            }

            self.get_logger().info(
                f"Loaded baseline with {len(self.world_baseline_mean_counts)} cells"
            )

        except Exception as e:
            self.get_logger().warn(f"Could not load baseline: {e}")

    def save_baseline(self):
        if self.learning_count <= 0:
            self.get_logger().warn("Skipping baseline save because no learning frames were accumulated.")
            return

        mean = {
            key: value / float(self.learning_count)
            for key, value in self.learning_sums.items()
        }

        payload = {
            "cell_size": self.world_cell_size,
            "learning_frames": self.learning_count,
            "target_frame": self.target_frame,
            "input_source": self.current_input_source,
            "baseline_mean_counts": mean,
            "timestamp": time.time(),
        }

        os.makedirs(os.path.dirname(self.world_baseline_json), exist_ok=True)

        tmp = self.world_baseline_json + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        os.replace(tmp, self.world_baseline_json)

        self.get_logger().warn(f"[BASELINE SAVED] {self.world_baseline_json}")

    def write_json(self, payload):
        try:
            os.makedirs(os.path.dirname(self.output_json), exist_ok=True)

            tmp = self.output_json + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)

            os.replace(tmp, self.output_json)

        except Exception as e:
            self.get_logger().warn(f"Could not write JSON: {e}")

    def frange(self, start, stop, step):
        values = []
        x = start

        while x <= stop + 1e-9:
            values.append(x)
            x += step

        return values

    def alignment_score(self, points, theta, tx, ty):
        if not points:
            return 0.0

        c = math.cos(theta)
        s = math.sin(theta)

        hits = 0.0

        for x, y in points:
            bx = c * x - s * y + tx
            by = s * x + c * y + ty

            ix, iy = self.cell_index(bx, by)

            support = self.local_sum(
                self.world_baseline_mean_counts,
                ix,
                iy,
                1,
            )

            if support >= self.baseline_exclusion_threshold:
                hits += 1.0
            elif support > 0.0:
                hits += 0.35

        return hits / float(len(points))

    def search_alignment_window(
        self,
        points,
        center_theta,
        center_tx,
        center_ty,
        angle_window_deg,
        angle_step_deg,
        translation_window_m,
        translation_step_m,
    ):
        best_theta = center_theta
        best_tx = center_tx
        best_ty = center_ty
        best_score = -1.0

        angles = self.frange(
            -angle_window_deg,
            angle_window_deg,
            angle_step_deg,
        )

        translations = self.frange(
            -translation_window_m,
            translation_window_m,
            translation_step_m,
        )

        for a_deg in angles:
            theta = center_theta + math.radians(a_deg)

            for dx in translations:
                tx = center_tx + dx

                for dy in translations:
                    ty = center_ty + dy

                    score = self.alignment_score(points, theta, tx, ty)

                    if score > best_score:
                        best_score = score
                        best_theta = theta
                        best_tx = tx
                        best_ty = ty

        return best_theta, best_tx, best_ty, best_score

    def auto_align(self):
        if not self.world_baseline_mean_counts:
            return

        if not self.current_points_with_z:
            return

        if self.loop_counter % self.alignment_every_n_frames != 0:
            return

        all_points = [(x, y) for x, y, _z in self.current_points_with_z]

        if self.alignment_use_outside_roi and self.alignment_ready:
            outside = []

            for x, y in all_points:
                bx, by = self.align_current_to_baseline(x, y)

                if not self.point_in_roi(bx, by):
                    outside.append((x, y))

            if len(outside) >= self.alignment_min_points:
                candidate_points = outside
            else:
                candidate_points = all_points
        else:
            candidate_points = all_points

        if len(candidate_points) < self.alignment_min_points:
            self.get_logger().warn(
                f"Auto-align skipped: only {len(candidate_points)} points",
                throttle_duration_sec=2.0,
            )
            return

        if len(candidate_points) > self.alignment_max_points:
            step = max(1, len(candidate_points) // self.alignment_max_points)
            candidate_points = candidate_points[::step]

        center_theta = self.alignment_rotation_rad if self.alignment_ready else 0.0
        center_tx = self.alignment_tx if self.alignment_ready else 0.0
        center_ty = self.alignment_ty if self.alignment_ready else 0.0

        coarse_theta, coarse_tx, coarse_ty, _ = self.search_alignment_window(
            candidate_points,
            center_theta,
            center_tx,
            center_ty,
            self.alignment_coarse_angle_window_deg,
            self.alignment_coarse_angle_step_deg,
            self.alignment_coarse_translation_window_m,
            self.alignment_coarse_translation_step_m,
        )

        fine_theta, fine_tx, fine_ty, fine_score = self.search_alignment_window(
            candidate_points,
            coarse_theta,
            coarse_tx,
            coarse_ty,
            self.alignment_fine_angle_window_deg,
            self.alignment_fine_angle_step_deg,
            self.alignment_fine_translation_window_m,
            self.alignment_fine_translation_step_m,
        )

        if fine_score < self.alignment_accept_min_score:
            self.get_logger().warn(
                f"Auto-align rejected: score={fine_score:.3f}",
                throttle_duration_sec=2.0,
            )
            return

        if not self.alignment_ready:
            self.alignment_rotation_rad = fine_theta
            self.alignment_tx = fine_tx
            self.alignment_ty = fine_ty
            self.alignment_ready = True
        else:
            alpha = max(0.01, min(1.0, self.alignment_smoothing_alpha))

            self.alignment_rotation_rad = (
                (1.0 - alpha) * self.alignment_rotation_rad
                + alpha * fine_theta
            )
            self.alignment_tx = (1.0 - alpha) * self.alignment_tx + alpha * fine_tx
            self.alignment_ty = (1.0 - alpha) * self.alignment_ty + alpha * fine_ty

        self.last_alignment_score = fine_score

        self.get_logger().info(
            f"Auto-align OK | rot={math.degrees(self.alignment_rotation_rad):.2f} deg | "
            f"tx={self.alignment_tx:.3f} | ty={self.alignment_ty:.3f} | "
            f"score={fine_score:.3f}",
            throttle_duration_sec=1.0,
        )

    def baseline_nearby(self, ix, iy, radius):
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if float(self.world_baseline_mean_counts.get(f"{ix + dx},{iy + dy}", 0.0)) > 0.0:
                    return True

        return False

    def compute_novel_cells(self):
        if not self.alignment_ready:
            return {}

        current_counts: Dict[str, int] = {}

        for x, y, _z in self.current_points_with_z:
            bx, by = self.align_current_to_baseline(x, y)

            if not self.point_in_roi(bx, by):
                continue

            key = self.cell_key(bx, by)
            current_counts[key] = current_counts.get(key, 0) + 1

        current_float = {
            key: float(value)
            for key, value in current_counts.items()
        }

        novel: Dict[str, float] = {}

        for key, value in current_counts.items():
            ix, iy = map(int, key.split(","))

            if value < self.novel_cell_min_points:
                continue

            current_local = self.local_sum(
                current_float,
                ix,
                iy,
                self.novel_compare_radius_cells,
            )

            if current_local < self.novel_local_min_points:
                continue

            baseline_local = self.local_sum(
                self.world_baseline_mean_counts,
                ix,
                iy,
                self.baseline_exclusion_radius_cells,
            )

            if baseline_local >= self.baseline_presence_threshold:
                continue

            if self.baseline_nearby(
                ix,
                iy,
                self.min_distance_from_baseline_cells,
            ):
                continue

            novel[key] = current_local

        return novel

    def build_clusters(self, novel_cells: Dict[str, float]):
        unvisited = set(novel_cells.keys())
        clusters = []
        cluster_id = 1

        while unvisited:
            seed = unvisited.pop()
            queue = [seed]
            keys = [seed]

            while queue:
                current = queue.pop()
                ix, iy = map(int, current.split(","))

                for nx in range(ix - 1, ix + 2):
                    for ny in range(iy - 1, iy + 2):
                        nkey = f"{nx},{ny}"

                        if nkey in unvisited:
                            unvisited.remove(nkey)
                            queue.append(nkey)
                            keys.append(nkey)

            total_weight = 0.0
            wx = 0.0
            wy = 0.0
            inside = 0

            for key in keys:
                value = float(novel_cells[key])
                ix, iy = map(int, key.split(","))

                cx = (ix + 0.5) * self.world_cell_size
                cy = (iy + 0.5) * self.world_cell_size

                total_weight += value
                wx += cx * value
                wy += cy * value

                if self.point_in_roi(cx, cy):
                    inside += 1

            centroid_x = wx / max(1.0, total_weight)
            centroid_y = wy / max(1.0, total_weight)

            point_count = int(round(total_weight))
            cell_count = len(keys)
            inside_ratio = inside / float(max(1, cell_count))

            detected = (
                point_count >= self.cluster_min_points
                and cell_count >= self.cluster_min_cells
                and inside_ratio >= self.cluster_inside_ratio_threshold
            )

            clusters.append(
                NovelCluster(
                    id=cluster_id,
                    point_count=point_count,
                    cell_count=cell_count,
                    centroid_x=centroid_x,
                    centroid_y=centroid_y,
                    inside_ratio=inside_ratio,
                    detected=detected,
                )
            )

            cluster_id += 1

        clusters.sort(
            key=lambda c: (c.detected, c.point_count, c.cell_count),
            reverse=True,
        )

        return clusters

    def setup_popup(self):
        if not self.show_popup:
            return

        if not MATPLOTLIB_AVAILABLE:
            self.get_logger().warn("matplotlib not available; popup disabled.")
            self.show_popup = False
            return

        if not os.environ.get("DISPLAY"):
            self.get_logger().warn("DISPLAY not set; popup disabled.")
            self.show_popup = False
            return

        plt.ion()
        self.figure, (self.ax_baseline, self.ax_current, self.ax_novel) = plt.subplots(
            1,
            3,
            figsize=(18, 6),
        )

        try:
            self.figure.canvas.manager.set_window_title("LiDAR Guard Viewer")
        except Exception:
            pass

    def update_popup(self):
        if (
            not self.show_popup
            or self.figure is None
            or self.ax_baseline is None
            or self.ax_current is None
            or self.ax_novel is None
        ):
            return

        self.ax_baseline.clear()
        self.ax_current.clear()
        self.ax_novel.clear()

        if self.learn_mode and self.learning_count > 0:
            baseline_counts_for_plot = {
                key: value / float(max(1, self.learning_count))
                for key, value in self.learning_sums.items()
            }
        else:
            baseline_counts_for_plot = self.world_baseline_mean_counts

        # 1) Baseline aprendido.
        if baseline_counts_for_plot:
            xs = []
            ys = []
            cs = []

            for key, value in baseline_counts_for_plot.items():
                ix, iy = map(int, key.split(","))
                xs.append((ix + 0.5) * self.world_cell_size)
                ys.append((iy + 0.5) * self.world_cell_size)
                cs.append(value)

            self.ax_baseline.scatter(xs, ys, c=cs, s=8, cmap="Greys", alpha=0.85)

        # 2) Mapa actual alineado.
        current_cloud_xs = []
        current_cloud_ys = []
        current_scan_xs = []
        current_scan_ys = []

        for x, y, _z in self.current_cloud_points_with_z:
            if self.alignment_ready:
                x, y = self.align_current_to_baseline(x, y)
            current_cloud_xs.append(x)
            current_cloud_ys.append(y)

        for x, y, _z in self.current_scan_points_with_z:
            if self.alignment_ready:
                x, y = self.align_current_to_baseline(x, y)
            current_scan_xs.append(x)
            current_scan_ys.append(y)

        if current_cloud_xs:
            self.ax_current.scatter(
                current_cloud_xs,
                current_cloud_ys,
                s=8,
                alpha=0.55,
                label="pointcloud",
            )

        if current_scan_xs:
            self.ax_current.scatter(
                current_scan_xs,
                current_scan_ys,
                s=12,
                alpha=0.75,
                label="scan",
            )

        # 3) Celdas nuevas.
        if self.current_novel_cells_for_plot:
            nx = []
            ny = []
            nc = []

            for key, value in self.current_novel_cells_for_plot.items():
                ix, iy = map(int, key.split(","))
                nx.append((ix + 0.5) * self.world_cell_size)
                ny.append((iy + 0.5) * self.world_cell_size)
                nc.append(value)

            self.ax_novel.scatter(
                nx,
                ny,
                c=nc,
                s=35,
                cmap="autumn",
                marker="s",
                alpha=0.8,
                label="novel cells",
            )

        # Clusters detectados.
        for cluster in self.latest_clusters:
            if not cluster.detected:
                continue

            radius = max(0.15, 0.06 * math.sqrt(float(cluster.cell_count)))

            for ax in (self.ax_current, self.ax_novel):
                circle = plt.Circle(
                    (cluster.centroid_x, cluster.centroid_y),
                    radius,
                    fill=False,
                    linewidth=2.5,
                )
                ax.add_patch(circle)
                ax.text(
                    cluster.centroid_x,
                    cluster.centroid_y + radius + 0.05,
                    f"C{cluster.id}",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                    weight="bold",
                )

        # ROI.
        polygon = self.polygon_closed()
        if polygon:
            px = [p[0] for p in polygon]
            py = [p[1] for p in polygon]

            for ax in (self.ax_baseline, self.ax_current, self.ax_novel):
                ax.plot(px, py, linewidth=2.0)

        title_state = "PERSON DETECTED" if self.latched_detected else "NO PERSON"

        self.ax_baseline.set_title("Mapa aprendido")
        self.ax_current.set_title(
            f"Mapa actual alineado\n"
            f"rot={math.degrees(self.alignment_rotation_rad):.2f}°, "
            f"tx={self.alignment_tx:.2f}, ty={self.alignment_ty:.2f}, "
            f"score={self.last_alignment_score:.2f}"
        )
        self.ax_novel.set_title(f"Zonas nuevas | {title_state}")

        for ax in (self.ax_baseline, self.ax_current, self.ax_novel):
            ax.set_aspect("equal", adjustable="box")
            ax.grid(True, alpha=0.25)

        if current_cloud_xs or current_scan_xs:
            self.ax_current.legend(loc="upper right", fontsize=8)

        if self.current_novel_cells_for_plot:
            self.ax_novel.legend(loc="upper right", fontsize=8)

        self.figure.tight_layout()
        self.figure.canvas.draw_idle()

        self.popup_update_counter += 1
        should_save_debug = (
            self.save_debug_image
            and bool(self.debug_image_path)
            and self.debug_image_stride > 0
            and (self.popup_update_counter % self.debug_image_stride) == 0
        )

        if should_save_debug:
            try:
                os.makedirs(os.path.dirname(self.debug_image_path), exist_ok=True)
                self.figure.savefig(self.debug_image_path, dpi=140)
            except Exception as e:
                self.get_logger().warn(
                    f"Could not save debug image {self.debug_image_path}: {e}",
                    throttle_duration_sec=5.0,
                )

        plt.pause(0.001)

    def learning_step(self):
        self.extract_current_points()

        if self.learning_warmup_count < self.learning_warmup_frames:
            self.learning_warmup_count += 1

            self.get_logger().info(
                f"[LEARNING WARMUP] {self.learning_warmup_count}/{self.learning_warmup_frames} | "
                f"points={len(self.current_points_with_z)} | source={self.current_input_source}",
                throttle_duration_sec=1.0,
            )

            self.write_json(
                {
                    "status": "learning",
                    "person_detected": False,
                    "reason": "learning_warmup",
                    "learning_warmup_counter": self.learning_warmup_count,
                    "learning_warmup_frames": self.learning_warmup_frames,
                    "learning_frames_done": self.learning_count,
                    "learning_frames_target": self.learning_frames,
                    "current_point_count": len(self.current_points_with_z),
                    "timestamp": time.time(),
                }
            )

            if self.show_popup and self.loop_counter % self.popup_stride == 0:
                self.update_popup()

            return

        counts = self.points_to_counts(self.current_points_with_z)

        for key, value in counts.items():
            self.learning_sums[key] = self.learning_sums.get(key, 0) + int(value)

        self.learning_count += 1

        self.get_logger().info(
            f"[LEARNING BASELINE] {self.learning_count}/{self.learning_frames} | "
            f"points={len(self.current_points_with_z)} | cells={len(counts)}",
            throttle_duration_sec=1.0,
        )

        self.write_json(
            {
                "status": "learning",
                "person_detected": False,
                "reason": "learning_world_baseline",
                "current_point_count": len(self.current_points_with_z),
                "current_cell_count": len(counts),
                "learning_frames_done": self.learning_count,
                "learning_frames_target": self.learning_frames,
                "timestamp": time.time(),
            }
        )

        if self.show_popup and self.loop_counter % self.popup_stride == 0:
            self.update_popup()

        if self.learning_count >= self.learning_frames and not self.learning_done:
            self.save_baseline()
            self.learning_done = True
            self.get_logger().warn(
                "[LEARNING DONE] Baseline saved successfully. You can stop the node with Ctrl+C."
            )

    def detection_step(self):
        self.extract_current_points()

        self.auto_align()

        novel_cells = self.compute_novel_cells()
        self.current_novel_cells_for_plot = novel_cells

        self.latest_clusters = self.build_clusters(novel_cells)

        detected_now = any(c.detected for c in self.latest_clusters)

        if detected_now:
            self.confirm_counter += 1
            self.clear_counter = 0

            if self.confirm_counter >= self.confirm_frames:
                self.latched_detected = True
        else:
            self.clear_counter += 1
            self.confirm_counter = 0

            if self.clear_counter >= self.clear_frames:
                self.latched_detected = False

        payload = {
            "status": "online",
            "input_source": self.current_input_source,
            "person_detected": self.latched_detected,
            "count": 1 if self.latched_detected else 0,
            "occupied_tiles": [],
            "current_point_count": len(self.current_points_with_z),
            "novel_cell_count": len(novel_cells),
            "current_novel_point_count": int(sum(novel_cells.values())),
            "clusters": [c.to_dict() for c in self.latest_clusters],
            "detected_cluster_ids": [c.id for c in self.latest_clusters if c.detected],
            "alignment": {
                "ready": self.alignment_ready,
                "rotation_deg": round(math.degrees(self.alignment_rotation_rad), 3),
                "tx": round(self.alignment_tx, 4),
                "ty": round(self.alignment_ty, 4),
                "score": round(self.last_alignment_score, 3),
                "mode": "automatic_runtime_grid_matching",
            },
            "interest_polygon": [
                [round(x, 3), round(y, 3)]
                for x, y in self.interest_polygon
            ],
            "debug_image_path": self.debug_image_path,
            "timestamp": time.time(),
        }

        self.write_json(payload)

        self.get_logger().info(
            f"[DETECTION] person={self.latched_detected} | "
            f"points={len(self.current_points_with_z)} | "
            f"novel_cells={len(novel_cells)} | "
            f"clusters={len(self.latest_clusters)} | "
            f"align_ready={self.alignment_ready}",
            throttle_duration_sec=1.0,
        )

        if self.show_popup and self.loop_counter % self.popup_stride == 0:
            self.update_popup()

    def sensor_online(self):
        source = self.active_input_source()

        if source is None:
            return False

        self.current_input_source = source

        if not self.update_robot_pose_from_tf():
            return False

        if self.last_odom_wall_time is None:
            return False

        if time.time() - self.last_odom_wall_time > self.stale_odom_sec:
            return False

        return True

    def on_timer(self):
        self.loop_counter += 1

        if not self.sensor_online():
            self.write_json(
                {
                    "status": "offline",
                    "person_detected": False,
                    "count": 0,
                    "occupied_tiles": [],
                    "reason": "stale_or_missing_data",
                    "timestamp": time.time(),
                }
            )
            return

        stamp_key = self.current_stamp_key(self.current_input_source)

        if stamp_key is None:
            return

        if stamp_key == self.last_processed_stamp_key:
            return

        if self.learn_mode:
            self.learning_step()
        else:
            self.detection_step()

        self.last_processed_stamp_key = stamp_key


def main(args=None):
    rclpy.init(args=args)

    node = LidarStaticMapGuard()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node.destroy_node()
        except Exception:
            pass

        try:
            rclpy.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    main()