#!/usr/bin/env python3
import json
import math
import time
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy

from nav_msgs.msg import OccupancyGrid
from sensor_msgs.msg import LaserScan
from geometry_msgs.msg import TransformStamped

import tf2_ros


def yaw_from_quat(q) -> float:
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


ROS_WS_ROOT = Path(__file__).resolve().parents[1]

class SlamLiveViewerDetector(Node):
    def __init__(self):
        super().__init__("slam_live_viewer_detector")

        self.declare_parameter("map_topic", "/map")
        self.declare_parameter("scan_topic", "/scan")
        self.declare_parameter("target_frame", "map")
        self.declare_parameter("robot_frame", "base_link")

        self.declare_parameter(
            "state_path",
            str(ROS_WS_ROOT / "lidar_outputs" / "live_lidar_state.json"),
        )

        self.declare_parameter(
            "baseline_path",
            str(ROS_WS_ROOT / "lidar_outputs" / "static_baseline_slam.npz"),
        )

        self.declare_parameter("window_size_px", 900)
        self.declare_parameter("map_margin_px", 90)
        self.declare_parameter("max_display_scale", 5.0)

        self.declare_parameter("baseline_resolution_m", 0.10)
        self.declare_parameter("novelty_distance_m", 0.22)
        self.declare_parameter("wall_margin_m", 0.25)
        self.declare_parameter("novelty_required_points", 10)
        self.declare_parameter("novelty_cluster_radius_m", 0.45)
        self.declare_parameter("confirmation_frames", 3)

        self.map_topic = self.get_parameter("map_topic").value
        self.scan_topic = self.get_parameter("scan_topic").value
        self.target_frame = self.get_parameter("target_frame").value
        self.robot_frame = self.get_parameter("robot_frame").value

        self.state_path = Path(self.get_parameter("state_path").value)
        self.baseline_path = Path(self.get_parameter("baseline_path").value)

        self.window_size_px = int(self.get_parameter("window_size_px").value)
        self.map_margin_px = int(self.get_parameter("map_margin_px").value)
        self.max_display_scale = float(self.get_parameter("max_display_scale").value)

        self.baseline_resolution_m = float(self.get_parameter("baseline_resolution_m").value)
        self.novelty_distance_m = float(self.get_parameter("novelty_distance_m").value)
        self.wall_margin_m = float(self.get_parameter("wall_margin_m").value)
        self.novelty_required_points = int(self.get_parameter("novelty_required_points").value)
        self.novelty_cluster_radius_m = float(self.get_parameter("novelty_cluster_radius_m").value)
        self.confirmation_frames = int(self.get_parameter("confirmation_frames").value)

        self.latest_map: Optional[OccupancyGrid] = None
        self.latest_scan: Optional[LaserScan] = None

        self.baseline_ready = False
        self.baseline_points_q = set()
        self.baseline_map_data = None
        self.baseline_width = None
        self.baseline_height = None
        self.baseline_resolution = None
        self.baseline_origin_x = None
        self.baseline_origin_y = None
        self.baseline_occupied_inflated = None

        self.confirm_counter = 0
        self.last_detected = False
        self.last_reason = "startup"
        self.last_novel_points: List[Tuple[float, float]] = []

        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

        map_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

        scan_qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
            history=HistoryPolicy.KEEP_LAST,
            depth=5,
        )

        self.create_subscription(OccupancyGrid, self.map_topic, self.on_map, map_qos)
        self.create_subscription(LaserScan, self.scan_topic, self.on_scan, scan_qos)

        self.window_name = "GO2 SLAM live map"
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL | cv2.WINDOW_GUI_NORMAL)
        cv2.resizeWindow(self.window_name, self.window_size_px, self.window_size_px)

        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.baseline_path.parent.mkdir(parents=True, exist_ok=True)

        self.try_load_baseline()

        self.timer = self.create_timer(0.10, self.on_timer)

        self.get_logger().warn("===============================================")
        self.get_logger().warn("GO2 SLAM live viewer + manual baseline detector")
        self.get_logger().warn(f"map_topic     = {self.map_topic}")
        self.get_logger().warn(f"scan_topic    = {self.scan_topic}")
        self.get_logger().warn(f"state_path    = {self.state_path}")
        self.get_logger().warn(f"baseline_path = {self.baseline_path}")
        self.get_logger().warn("Controles:")
        self.get_logger().warn("  b = guardar baseline estática")
        self.get_logger().warn("  r = borrar baseline")
        self.get_logger().warn("  q = salir")
        self.get_logger().warn("===============================================")

    def on_map(self, msg: OccupancyGrid):
        self.latest_map = msg

    def on_scan(self, msg: LaserScan):
        self.latest_scan = msg

    def transform_to_xyyaw(self, tf: TransformStamped) -> Tuple[float, float, float]:
        t = tf.transform.translation
        q = tf.transform.rotation
        return float(t.x), float(t.y), yaw_from_quat(q)

    def lookup_pose(self, target: str, source: str) -> Optional[Tuple[float, float, float]]:
        try:
            tf = self.tf_buffer.lookup_transform(
                target,
                source,
                rclpy.time.Time(),
                timeout=rclpy.duration.Duration(seconds=0.05),
            )
            return self.transform_to_xyyaw(tf)
        except Exception:
            return None

    def current_scan_points_in_map(self) -> List[Tuple[float, float]]:
        if self.latest_scan is None:
            return []

        scan = self.latest_scan
        scan_frame = scan.header.frame_id or self.robot_frame
        pose = self.lookup_pose(self.target_frame, scan_frame)

        if pose is None:
            return []

        tx, ty, yaw = pose
        cy = math.cos(yaw)
        sy = math.sin(yaw)

        points = []
        angle = scan.angle_min

        for r in scan.ranges:
            if math.isfinite(r) and scan.range_min <= r <= scan.range_max:
                lx = r * math.cos(angle)
                ly = r * math.sin(angle)

                wx = tx + cy * lx - sy * ly
                wy = ty + sy * lx + cy * ly
                points.append((wx, wy))

            angle += scan.angle_increment

        return points

    def quantize_point(self, x: float, y: float) -> Tuple[int, int]:
        res = self.baseline_resolution_m
        return int(round(x / res)), int(round(y / res))

    def try_load_baseline(self):
        if not self.baseline_path.exists():
            self.get_logger().warn("No hay baseline guardada todavía. Mapea y pulsa b.")
            return

        try:
            z = np.load(self.baseline_path, allow_pickle=True)

            points = z["points_q"]
            self.baseline_points_q = set((int(a), int(b)) for a, b in points)

            self.baseline_map_data = z["map_data"].astype(np.int16)
            self.baseline_width = int(z["width"])
            self.baseline_height = int(z["height"])
            self.baseline_resolution = float(z["resolution"])
            self.baseline_origin_x = float(z["origin_x"])
            self.baseline_origin_y = float(z["origin_y"])
            self.baseline_occupied_inflated = z["occupied_inflated"].astype(bool)

            self.baseline_ready = True
            self.get_logger().warn(
                f"Baseline cargada: {len(self.baseline_points_q)} puntos | "
                f"map={self.baseline_width}x{self.baseline_height}"
            )
        except Exception as e:
            self.get_logger().error(f"No he podido cargar baseline: {e}")
            self.baseline_ready = False

    def reset_baseline(self):
        self.baseline_ready = False
        self.baseline_points_q = set()
        self.baseline_map_data = None
        self.baseline_occupied_inflated = None

        try:
            if self.baseline_path.exists():
                self.baseline_path.unlink()
        except Exception:
            pass

        self.confirm_counter = 0
        self.get_logger().warn("Baseline borrada. Vuelve a mapear y pulsa b.")

    def build_inflated_occupied_mask(self, data: np.ndarray, resolution: float) -> np.ndarray:
        occupied = data >= 50

        radius_cells = max(1, int(math.ceil(self.wall_margin_m / resolution)))
        kernel_size = radius_cells * 2 + 1

        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        inflated = cv2.dilate(occupied.astype(np.uint8), kernel, iterations=1).astype(bool)

        return inflated

    def capture_baseline(self):
        if self.latest_map is None:
            self.get_logger().warn("No puedo capturar baseline: todavía no hay /map.")
            return

        points = self.current_scan_points_in_map()
        if len(points) < 20:
            self.get_logger().warn(
                f"No capturo baseline: hay pocos puntos de scan en map ({len(points)})."
            )
            return

        m = self.latest_map
        data = np.array(m.data, dtype=np.int16).reshape((m.info.height, m.info.width))

        self.baseline_map_data = data.copy()
        self.baseline_width = int(m.info.width)
        self.baseline_height = int(m.info.height)
        self.baseline_resolution = float(m.info.resolution)
        self.baseline_origin_x = float(m.info.origin.position.x)
        self.baseline_origin_y = float(m.info.origin.position.y)

        self.baseline_occupied_inflated = self.build_inflated_occupied_mask(
            self.baseline_map_data,
            self.baseline_resolution,
        )

        self.baseline_points_q = set(self.quantize_point(x, y) for x, y in points)
        self.baseline_ready = True
        self.confirm_counter = 0

        arr = np.array(list(self.baseline_points_q), dtype=np.int32)

        np.savez_compressed(
            self.baseline_path,
            points_q=arr,
            map_data=self.baseline_map_data,
            width=self.baseline_width,
            height=self.baseline_height,
            resolution=self.baseline_resolution,
            origin_x=self.baseline_origin_x,
            origin_y=self.baseline_origin_y,
            occupied_inflated=self.baseline_occupied_inflated,
        )

        self.get_logger().warn(
            f"Baseline guardada: {len(self.baseline_points_q)} puntos | "
            f"map={self.baseline_width}x{self.baseline_height} | {self.baseline_path}"
        )

    def baseline_world_to_cell(self, x: float, y: float) -> Optional[Tuple[int, int]]:
        if self.baseline_map_data is None:
            return None

        mx = int((x - self.baseline_origin_x) / self.baseline_resolution)
        my = int((y - self.baseline_origin_y) / self.baseline_resolution)

        if 0 <= mx < self.baseline_width and 0 <= my < self.baseline_height:
            return mx, my

        return None

    def is_free_in_baseline(self, x: float, y: float) -> bool:
        cell = self.baseline_world_to_cell(x, y)
        if cell is None:
            return False

        mx, my = cell
        value = int(self.baseline_map_data[my, mx])

        return 0 <= value < 50

    def is_far_from_baseline_walls(self, x: float, y: float) -> bool:
        cell = self.baseline_world_to_cell(x, y)
        if cell is None:
            return False

        mx, my = cell
        return not bool(self.baseline_occupied_inflated[my, mx])

    def has_near_baseline_scan_point(self, x: float, y: float) -> bool:
        qx, qy = self.quantize_point(x, y)

        search = int(math.ceil(self.novelty_distance_m / self.baseline_resolution_m))

        for dx in range(-search, search + 1):
            for dy in range(-search, search + 1):
                if (qx + dx, qy + dy) in self.baseline_points_q:
                    return True

        return False

    def largest_cluster(self, points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
        if not points:
            return []

        best = []

        for px, py in points:
            cluster = []
            for qx, qy in points:
                if math.hypot(px - qx, py - qy) <= self.novelty_cluster_radius_m:
                    cluster.append((qx, qy))

            if len(cluster) > len(best):
                best = cluster

        return best

    def detect_novelty(self) -> Tuple[bool, List[Tuple[float, float]], str]:
        if not self.baseline_ready:
            self.confirm_counter = 0
            return False, [], "baseline_not_ready"

        points = self.current_scan_points_in_map()

        candidates = []

        for x, y in points:
            if not self.is_free_in_baseline(x, y):
                continue

            if not self.is_far_from_baseline_walls(x, y):
                continue

            if self.has_near_baseline_scan_point(x, y):
                continue

            candidates.append((x, y))

        cluster = self.largest_cluster(candidates)

        if len(cluster) >= self.novelty_required_points:
            self.confirm_counter += 1
            if self.confirm_counter >= self.confirmation_frames:
                return True, cluster, f"confirmed_cluster_{len(cluster)}"
            return False, [], f"candidate_{len(cluster)}_confirm_{self.confirm_counter}"
        else:
            self.confirm_counter = 0
            return False, [], f"no_cluster_{len(cluster)}"

    def map_to_base_image(self) -> Optional[np.ndarray]:
        if self.latest_map is None:
            return None

        m = self.latest_map
        data = np.array(m.data, dtype=np.int16).reshape((m.info.height, m.info.width))

        img = np.zeros((m.info.height, m.info.width, 3), dtype=np.uint8)

        unknown = data < 0
        free = (data >= 0) & (data < 50)
        occupied = data >= 50

        img[unknown] = (155, 155, 155)
        img[free] = (250, 250, 250)
        img[occupied] = (20, 20, 20)

        img = np.flipud(img).copy()
        return img

    def map_world_to_cell_current(self, x: float, y: float) -> Optional[Tuple[int, int]]:
        if self.latest_map is None:
            return None

        m = self.latest_map

        mx = int((x - m.info.origin.position.x) / m.info.resolution)
        my = int((y - m.info.origin.position.y) / m.info.resolution)

        if 0 <= mx < m.info.width and 0 <= my < m.info.height:
            return mx, my

        return None

    def cell_to_base_img(self, mx: int, my: int, h: int) -> Tuple[int, int]:
        return mx, h - 1 - my

    def base_to_canvas(
        self,
        px: int,
        py: int,
        offset_x: int,
        offset_y: int,
        scale: float,
    ) -> Tuple[int, int]:
        return int(offset_x + px * scale), int(offset_y + py * scale)

    def draw_point_world(
        self,
        canvas: np.ndarray,
        x: float,
        y: float,
        color: Tuple[int, int, int],
        radius: int,
        offset_x: int,
        offset_y: int,
        scale: float,
        base_h: int,
    ):
        cell = self.map_world_to_cell_current(x, y)
        if cell is None:
            return

        mx, my = cell
        px, py = self.cell_to_base_img(mx, my, base_h)
        cx, cy = self.base_to_canvas(px, py, offset_x, offset_y, scale)

        if 0 <= cx < canvas.shape[1] and 0 <= cy < canvas.shape[0]:
            cv2.circle(canvas, (cx, cy), radius, color, -1)

    def render(self, detected: bool, novel_points: List[Tuple[float, float]]) -> np.ndarray:
        base = self.map_to_base_image()

        canvas_size = self.window_size_px
        canvas = np.full((canvas_size, canvas_size, 3), 245, dtype=np.uint8)

        if base is None:
            cv2.putText(
                canvas,
                "Waiting for /map from slam_toolbox...",
                (80, canvas_size // 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 0, 180),
                2,
            )
            return canvas

        base_h, base_w = base.shape[:2]

        available = canvas_size - 2 * self.map_margin_px
        scale = min(available / max(base_w, 1), available / max(base_h, 1))
        scale = min(scale, self.max_display_scale)
        scale = max(scale, 1.0)

        disp_w = int(base_w * scale)
        disp_h = int(base_h * scale)

        resized = cv2.resize(base, (disp_w, disp_h), interpolation=cv2.INTER_NEAREST)

        offset_x = (canvas_size - disp_w) // 2
        offset_y = (canvas_size - disp_h) // 2

        canvas[offset_y:offset_y + disp_h, offset_x:offset_x + disp_w] = resized

        cv2.rectangle(
            canvas,
            (offset_x, offset_y),
            (offset_x + disp_w, offset_y + disp_h),
            (80, 80, 80),
            1,
        )

        robot_pose = self.lookup_pose(self.target_frame, self.robot_frame)

        for x, y in self.current_scan_points_in_map()[::2]:
            self.draw_point_world(
                canvas, x, y,
                color=(0, 170, 0),
                radius=1,
                offset_x=offset_x,
                offset_y=offset_y,
                scale=scale,
                base_h=base_h,
            )

        if detected:
            for x, y in novel_points:
                self.draw_point_world(
                    canvas, x, y,
                    color=(0, 0, 255),
                    radius=2,
                    offset_x=offset_x,
                    offset_y=offset_y,
                    scale=scale,
                    base_h=base_h,
                )

        if robot_pose is not None:
            rx, ry, yaw = robot_pose
            cell = self.map_world_to_cell_current(rx, ry)

            if cell is not None:
                mx, my = cell
                px, py = self.cell_to_base_img(mx, my, base_h)
                cx, cy = self.base_to_canvas(px, py, offset_x, offset_y, scale)

                cv2.circle(canvas, (cx, cy), 5, (255, 0, 0), -1)

                arrow_len = 28
                hx = int(cx + arrow_len * math.cos(-yaw))
                hy = int(cy - arrow_len * math.sin(yaw))
                cv2.arrowedLine(canvas, (cx, cy), (hx, hy), (255, 0, 0), 3, tipLength=0.35)

        return canvas

    def write_state(self, detected: bool, points: List[Tuple[float, float]], reason: str):
        """
        Escribe el estado vivo del detector LiDAR.

        IMPORTANTE:
        - person_detected es el campo principal para otros scripts.
        - detected / obstacle_detected se mantienen como alias de compatibilidad.
        - position_map guarda el centroide de la detección en frame map.
        """

        now = time.time()

        if detected and points:
            cx = float(sum(p[0] for p in points) / len(points))
            cy = float(sum(p[1] for p in points) / len(points))

            position_map = {
                "x": cx,
                "y": cy,
                "frame": self.target_frame,
                "meaning": "centroid of novel LiDAR cluster in map frame",
            }

            person = {
                "detected": True,
                "position_map": position_map,
                "cluster_size": int(len(points)),
            }

            label = "person"

        else:
            position_map = None
            person = {
                "detected": False,
                "position_map": None,
                "cluster_size": 0,
            }
            label = "clear"

        payload = {
            # Campo principal para el autómata / scripts externos.
            "person_detected": bool(detected),

            # Alias de compatibilidad con lo que ya estabas usando.
            "detected": bool(detected),
            "obstacle_detected": bool(detected),

            # Etiqueta semántica.
            "label": label,
            "source": "lidar",
            "detector": "slam_lidar_novelty",

            # Tiempo.
            "timestamp": now,
            "wall_time": time.strftime("%Y-%m-%d %H:%M:%S"),

            # Estado de baseline / razón.
            "baseline_ready": bool(self.baseline_ready),
            "reason": reason,

            # Información útil para consumo posterior.
            "person": person,
            "position_map": position_map,
            "count": int(len(points)),

            # Lista de puntos detectados, limitada para no inflar el JSON.
            "points_map": [
                {
                    "x": float(x),
                    "y": float(y),
                    "frame": self.target_frame,
                }
                for x, y in points[:50]
            ],
        }

        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.state_path)

    def on_timer(self):
        detected, novel_points, reason = self.detect_novelty()

        self.last_detected = detected
        self.last_novel_points = novel_points
        self.last_reason = reason

        self.write_state(detected, novel_points, reason)

        img = self.render(detected, novel_points)

        cv2.imshow(self.window_name, img)
        key = cv2.waitKey(1) & 0xFF

        if key in (ord("q"), 27):
            self.write_state(False, [], "viewer_closed")
            rclpy.shutdown()

        elif key in (ord("b"), ord("B")):
            self.capture_baseline()

        elif key in (ord("r"), ord("R")):
            self.reset_baseline()

    def destroy_node(self):
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = SlamLiveViewerDetector()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node.write_state(False, [], "shutdown")
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
