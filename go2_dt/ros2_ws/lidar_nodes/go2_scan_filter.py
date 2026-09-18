#!/usr/bin/env python3

import math
from typing import List

import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSProfile,
    QoSReliabilityPolicy,
    QoSHistoryPolicy,
    QoSDurabilityPolicy,
)
from sensor_msgs.msg import LaserScan


def normalize_angle_rad(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


class Go2ScanFilter(Node):
    def __init__(self):
        super().__init__("go2_scan_filter")

        self.declare_parameter("input_scan_topic", "/scan_raw")
        self.declare_parameter("output_scan_topic", "/scan")

        self.declare_parameter("range_min_out", 0.30)
        self.declare_parameter("range_max_out", 20.0)

        self.declare_parameter("remove_close_radius_m", 0.25)

        self.declare_parameter("filter_front_cone", True)
        self.declare_parameter("front_cone_half_angle_rad", 0.70)
        self.declare_parameter("front_cone_distance_m", 0.60)

        self.declare_parameter("filter_rear_cone", False)
        self.declare_parameter("rear_cone_half_angle_rad", 0.70)
        self.declare_parameter("rear_cone_distance_m", 0.50)

        self.declare_parameter("replace_removed_with_inf", True)

        self.input_scan_topic = str(self.get_parameter("input_scan_topic").value)
        self.output_scan_topic = str(self.get_parameter("output_scan_topic").value)

        self.range_min_out = float(self.get_parameter("range_min_out").value)
        self.range_max_out = float(self.get_parameter("range_max_out").value)

        self.remove_close_radius_m = float(self.get_parameter("remove_close_radius_m").value)

        self.filter_front_cone = bool(self.get_parameter("filter_front_cone").value)
        self.front_cone_half_angle_rad = float(self.get_parameter("front_cone_half_angle_rad").value)
        self.front_cone_distance_m = float(self.get_parameter("front_cone_distance_m").value)

        self.filter_rear_cone = bool(self.get_parameter("filter_rear_cone").value)
        self.rear_cone_half_angle_rad = float(self.get_parameter("rear_cone_half_angle_rad").value)
        self.rear_cone_distance_m = float(self.get_parameter("rear_cone_distance_m").value)

        self.replace_removed_with_inf = bool(self.get_parameter("replace_removed_with_inf").value)

        qos = QoSProfile(
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=5,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            durability=QoSDurabilityPolicy.VOLATILE,
        )

        self.sub = self.create_subscription(
            LaserScan,
            self.input_scan_topic,
            self.callback,
            qos,
        )

        self.pub = self.create_publisher(
            LaserScan,
            self.output_scan_topic,
            qos,
        )

        self.get_logger().info(
            f"Go2ScanFilter activo: {self.input_scan_topic} -> {self.output_scan_topic} | "
            f"close<{self.remove_close_radius_m:.2f}m | "
            f"front={self.filter_front_cone} {self.front_cone_distance_m:.2f}m "
            f"half_angle={self.front_cone_half_angle_rad:.2f}rad | "
            f"rear={self.filter_rear_cone}"
        )

    def removed_value(self) -> float:
        return float("inf") if self.replace_removed_with_inf else 0.0

    def should_remove(self, r: float, angle: float) -> bool:
        if not math.isfinite(r):
            return False

        if r < self.remove_close_radius_m:
            return True

        if r < self.range_min_out or r > self.range_max_out:
            return True

        angle_norm = normalize_angle_rad(angle)

        if self.filter_front_cone:
            if abs(angle_norm) <= self.front_cone_half_angle_rad:
                if r <= self.front_cone_distance_m:
                    return True

        if self.filter_rear_cone:
            rear_error = abs(abs(angle_norm) - math.pi)
            if rear_error <= self.rear_cone_half_angle_rad:
                if r <= self.rear_cone_distance_m:
                    return True

        return False

    def callback(self, msg: LaserScan):
        out = LaserScan()
        out.header = msg.header

        out.angle_min = msg.angle_min
        out.angle_max = msg.angle_max
        out.angle_increment = msg.angle_increment
        out.time_increment = msg.time_increment
        out.scan_time = msg.scan_time

        out.range_min = self.range_min_out
        out.range_max = self.range_max_out

        filtered: List[float] = []
        angle = float(msg.angle_min)

        removed = 0

        for r in msg.ranges:
            rf = float(r)

            if self.should_remove(rf, angle):
                filtered.append(self.removed_value())
                removed += 1
            else:
                filtered.append(rf)

            angle += float(msg.angle_increment)

        out.ranges = filtered
        out.intensities = list(msg.intensities)

        self.pub.publish(out)

        self.get_logger().debug(
            f"scan filtered: total={len(msg.ranges)} removed={removed}"
        )


def main(args=None):
    rclpy.init(args=args)
    node = Go2ScanFilter()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node.destroy_node()
        except Exception:
            pass
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
