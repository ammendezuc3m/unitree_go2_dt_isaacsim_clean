#!/usr/bin/env python3
import json
import math
import os

import rclpy
from rclpy.node import Node

from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry


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


class RectangleWalkNode(Node):
    def __init__(self):
        super().__init__('rectangle_walk')

        self.declare_parameter('cmd_topic', '/cmd_vel_out')
        self.declare_parameter('odom_topic', '/odom')
        self.declare_parameter('control_json', os.path.expanduser('~/AlbertoDir/go2_dt/ros2_ws/src/go2_rect_motion/config/control.json'))
        self.declare_parameter('length', 2.0)
        self.declare_parameter('width', 1.0)
        self.declare_parameter('linear_speed', 0.20)
        self.declare_parameter('angular_speed', 0.45)
        self.declare_parameter('pos_tolerance', 0.03)
        self.declare_parameter('yaw_tolerance_deg', 4.0)
        self.declare_parameter('control_poll_hz', 5.0)
        self.declare_parameter('loop_hz', 20.0)

        self.cmd_topic = self.get_parameter('cmd_topic').get_parameter_value().string_value
        self.odom_topic = self.get_parameter('odom_topic').get_parameter_value().string_value
        self.control_json = self.get_parameter('control_json').get_parameter_value().string_value
        self.length = self.get_parameter('length').get_parameter_value().double_value
        self.width = self.get_parameter('width').get_parameter_value().double_value
        self.linear_speed = self.get_parameter('linear_speed').get_parameter_value().double_value
        self.angular_speed = self.get_parameter('angular_speed').get_parameter_value().double_value
        self.pos_tolerance = self.get_parameter('pos_tolerance').get_parameter_value().double_value
        self.yaw_tolerance = math.radians(
            self.get_parameter('yaw_tolerance_deg').get_parameter_value().double_value
        )
        self.control_poll_hz = self.get_parameter('control_poll_hz').get_parameter_value().double_value
        self.loop_hz = self.get_parameter('loop_hz').get_parameter_value().double_value

        self.cmd_pub = self.create_publisher(Twist, self.cmd_topic, 10)
        self.odom_sub = self.create_subscription(Odometry, self.odom_topic, self.odom_callback, 20)

        self.control_timer = self.create_timer(1.0 / self.control_poll_hz, self.read_control_json)
        self.loop_timer = self.create_timer(1.0 / self.loop_hz, self.control_loop)

        self.current_x = None
        self.current_y = None
        self.current_yaw = None

        self.motion_enabled = True

        self.state = 'MOVE_EDGE'
        self.edge_index = 0  # 0:length, 1:width, 2:length, 3:width
        self.edge_lengths = [self.length, self.width, self.length, self.width]

        self.segment_start_x = None
        self.segment_start_y = None
        self.turn_target_yaw = None

        self.get_logger().info(
            f'Rectangle walker started. cmd_topic={self.cmd_topic}, odom_topic={self.odom_topic}, '
            f'length={self.length}, width={self.width}'
        )

    def odom_callback(self, msg: Odometry):
        self.current_x = msg.pose.pose.position.x
        self.current_y = msg.pose.pose.position.y
        q = msg.pose.pose.orientation
        self.current_yaw = yaw_from_quaternion(q.x, q.y, q.z, q.w)

    def read_control_json(self):
        try:
            if not os.path.exists(self.control_json):
                return

            with open(self.control_json, 'r', encoding='utf-8') as f:
                data = json.load(f)

            command = str(data.get('command', 'continue')).strip().lower()

            if command == 'stop':
                if self.motion_enabled:
                    self.get_logger().info('External command STOP received.')
                self.motion_enabled = False
            elif command == 'continue':
                if not self.motion_enabled:
                    self.get_logger().info('External command CONTINUE received.')
                self.motion_enabled = True
            else:
                self.get_logger().warn(f'Unknown command in JSON: {command}')
        except Exception as e:
            self.get_logger().warn(f'Could not read control JSON: {e}')

    def publish_zero(self):
        self.cmd_pub.publish(Twist())

    def distance_from_segment_start(self) -> float:
        if self.segment_start_x is None or self.segment_start_y is None:
            return 0.0
        dx = self.current_x - self.segment_start_x
        dy = self.current_y - self.segment_start_y
        return math.sqrt(dx * dx + dy * dy)

    def init_segment_if_needed(self):
        if self.segment_start_x is None or self.segment_start_y is None:
            self.segment_start_x = self.current_x
            self.segment_start_y = self.current_y

    def init_turn_if_needed(self):
        if self.turn_target_yaw is None:
            self.turn_target_yaw = normalize_angle(self.current_yaw + math.pi / 2.0)

    def advance_to_next_edge(self):
        self.edge_index = (self.edge_index + 1) % 4
        self.state = 'MOVE_EDGE'
        self.segment_start_x = self.current_x
        self.segment_start_y = self.current_y
        self.turn_target_yaw = None
        self.get_logger().info(f'Starting edge {self.edge_index} of length {self.edge_lengths[self.edge_index]:.2f} m')

    def control_loop(self):

        if self.current_x is None or self.current_y is None or self.current_yaw is None:
            self.get_logger().info('Waiting for odom...', throttle_duration_sec=2.0)
            return
        if self.current_x is None or self.current_y is None or self.current_yaw is None:
            return

        if not self.motion_enabled:
            self.publish_zero()
            return

        target_length = self.edge_lengths[self.edge_index]

        if self.state == 'MOVE_EDGE':
            self.init_segment_if_needed()
            traveled = self.distance_from_segment_start()

            if traveled >= target_length - self.pos_tolerance:
                self.publish_zero()
                self.state = 'TURN_CORNER'
                self.turn_target_yaw = None
                self.get_logger().info(
                    f'Edge {self.edge_index} completed. Traveled={traveled:.3f} m. Starting turn.'
                )
                return

            cmd = Twist()
            cmd.linear.x = self.linear_speed
            cmd.angular.z = 0.0
            self.cmd_pub.publish(cmd)

        elif self.state == 'TURN_CORNER':
            self.init_turn_if_needed()
            yaw_error = normalize_angle(self.turn_target_yaw - self.current_yaw)

            if abs(yaw_error) <= self.yaw_tolerance:
                self.publish_zero()
                self.advance_to_next_edge()
                return

            cmd = Twist()
            cmd.linear.x = 0.0
            cmd.angular.z = self.angular_speed if yaw_error > 0.0 else -self.angular_speed
            self.cmd_pub.publish(cmd)

        else:
            self.get_logger().warn(f'Unknown state: {self.state}. Forcing stop.')
            self.publish_zero()

    def destroy_node(self):
        self.publish_zero()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = RectangleWalkNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.publish_zero()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()