#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist


class CmdVelOutRelay(Node):
    def __init__(self):
        super().__init__("cmd_vel_out_relay")

        self.declare_parameter("input_topic", "/cmd_vel_out")
        self.declare_parameter("output_topic", "/cmd_vel")

        self.input_topic = str(self.get_parameter("input_topic").value)
        self.output_topic = str(self.get_parameter("output_topic").value)

        self.pub = self.create_publisher(Twist, self.output_topic, 10)
        self.sub = self.create_subscription(Twist, self.input_topic, self.cb, 10)

        self.get_logger().warn(f"Relaying {self.input_topic} -> {self.output_topic}")

    def cb(self, msg: Twist):
        self.pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = CmdVelOutRelay()

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
