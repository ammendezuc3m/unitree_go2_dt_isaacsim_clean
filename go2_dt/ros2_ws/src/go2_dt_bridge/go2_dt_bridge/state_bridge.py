import rclpy
from rclpy.node import Node

from geometry_msgs.msg import PoseStamped, Vector3Stamped
from nav_msgs.msg import Odometry
from std_msgs.msg import Float32MultiArray

from go2_interfaces.msg import Go2State, IMU


class Go2DTBridge(Node):
    def __init__(self):
        super().__init__("go2_dt_bridge")

        self.last_odom = None
        self.last_go2 = None
        self.last_imu = None

        self.base_pose_pub = self.create_publisher(PoseStamped, "/go2_dt/base_pose", 10)
        self.feet_pub = self.create_publisher(Float32MultiArray, "/go2_dt/feet_body", 10)
        self.rpy_pub = self.create_publisher(Vector3Stamped, "/go2_dt/rpy", 10)

        self.create_subscription(Odometry, "/odom", self.odom_cb, 10)
        self.create_subscription(Go2State, "/go2_states", self.go2_cb, 10)
        self.create_subscription(IMU, "/imu", self.imu_cb, 10)

        # Publicamos a 50 Hz reutilizando el último dato recibido.
        self.timer = self.create_timer(0.02, self.publish_outputs)

        self.get_logger().info("Go2 DT bridge started")

    def odom_cb(self, msg: Odometry):
        self.last_odom = msg

    def go2_cb(self, msg: Go2State):
        self.last_go2 = msg

    def imu_cb(self, msg: IMU):
        self.last_imu = msg

    def publish_outputs(self):
        now = self.get_clock().now().to_msg()

        if self.last_odom is not None:
            pose_msg = PoseStamped()
            pose_msg.header.stamp = now
            pose_msg.header.frame_id = "odom"

            pose_msg.pose.position = self.last_odom.pose.pose.position
            pose_msg.pose.orientation = self.last_odom.pose.pose.orientation

            self.base_pose_pub.publish(pose_msg)

        if self.last_go2 is not None:
            feet_msg = Float32MultiArray()
            # Orden esperado:
            # [FL_x, FL_y, FL_z,
            #  FR_x, FR_y, FR_z,
            #  RL_x, RL_y, RL_z,
            #  RR_x, RR_y, RR_z]
            feet_msg.data = list(self.last_go2.foot_position_body)
            self.feet_pub.publish(feet_msg)

        if self.last_imu is not None:
            rpy_msg = Vector3Stamped()
            rpy_msg.header.stamp = now
            rpy_msg.header.frame_id = "base_link"

            # go2_interfaces/msg/IMU -> rpy es un array de 3 floats
            rpy_msg.vector.x = float(self.last_imu.rpy[0])  # roll
            rpy_msg.vector.y = float(self.last_imu.rpy[1])  # pitch
            rpy_msg.vector.z = float(self.last_imu.rpy[2])  # yaw

            self.rpy_pub.publish(rpy_msg)


def main(args=None):
    rclpy.init(args=args)
    node = Go2DTBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
