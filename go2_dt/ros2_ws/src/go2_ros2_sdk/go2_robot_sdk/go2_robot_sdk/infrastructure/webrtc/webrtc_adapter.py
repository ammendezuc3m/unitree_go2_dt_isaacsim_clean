# Copyright (c) 2024, RoboVerse community
# SPDX-License-Identifier: BSD-3-Clause

import asyncio
import json
import logging
from typing import Callable, Dict, Any

from ...domain.interfaces import IRobotDataReceiver, IRobotController
from ...domain.entities import RobotData, RobotConfig
from .go2_connection import Go2Connection
from ...application.utils.command_generator import gen_command, gen_mov_command
from ...domain.constants import ROBOT_CMD, RTC_TOPIC

logger = logging.getLogger(__name__)


class WebRTCAdapter(IRobotDataReceiver, IRobotController):
    """WebRTC adapter for robot communication"""

    def __init__(self, config: RobotConfig, on_validated_callback: Callable, on_video_frame_callback: Callable = None, event_loop=None):
        self.config = config
        self.connections: Dict[str, Go2Connection] = {}
        self.data_callback: Callable[[RobotData], None] = None
        self.webrtc_msgs = asyncio.Queue()
        self.on_validated_callback = on_validated_callback
        self.on_video_frame_callback = on_video_frame_callback

        if event_loop:
            self.main_loop = event_loop
        else:
            try:
                self.main_loop = asyncio.get_running_loop()
            except RuntimeError:
                self.main_loop = None

        logger.info(
            f"[DEBUG][WebRTCAdapter.__init__] conn_type={self.config.conn_type}, "
            f"robot_ips={self.config.robot_ip_list}, token={'set' if self.config.token else 'empty'}"
        )

    async def connect(self, robot_id: str) -> None:
        """Connect to robot via WebRTC"""
        try:
            robot_idx = int(robot_id)
            robot_ip = self.config.robot_ip_list[robot_idx]

            logger.info(
                f"[DEBUG][connect] Starting WebRTC connect to robot_id={robot_id}, "
                f"robot_ip={robot_ip}, token={'set' if self.config.token else 'empty'}"
            )

            conn = Go2Connection(
                robot_ip=robot_ip,
                robot_num=robot_id,
                token=self.config.token,
                on_validated=self._on_validated,
                on_message=self._on_data_channel_message,
                on_video_frame=self.on_video_frame_callback if self.config.enable_video else None,
                decode_lidar=self.config.decode_lidar,
            )

            self.connections[robot_id] = conn
            logger.info(f"[DEBUG][connect] Go2Connection object created for robot {robot_id}")

            await conn.connect()
            logger.info(f"[DEBUG][connect] conn.connect() finished for robot {robot_id}")

            result = await conn.disableTrafficSaving(True)
            logger.info(f"[DEBUG][connect] disableTrafficSaving(True) returned {result} for robot {robot_id}")

            logger.info(f"Connected to robot {robot_id} at {robot_ip}")

        except Exception as e:
            logger.error(f"Failed to connect to robot {robot_id}: {e}", exc_info=True)
            raise

    async def disconnect(self, robot_id: str) -> None:
        """Disconnect from robot"""
        if robot_id in self.connections:
            try:
                connection = self.connections[robot_id]
                logger.info(f"[DEBUG][disconnect] Disconnecting robot {robot_id}")

                if hasattr(connection, 'disconnect'):
                    await connection.disconnect()
                elif hasattr(connection, 'pc') and connection.pc:
                    await connection.pc.close()

                del self.connections[robot_id]
                logger.info(f"Disconnected from robot {robot_id}")

            except Exception as e:
                logger.error(f"Error disconnecting from robot {robot_id}: {e}", exc_info=True)

    def set_data_callback(self, callback: Callable[[RobotData], None]) -> None:
        """Set callback for data reception"""
        self.data_callback = callback
        logger.info("[DEBUG][set_data_callback] Data callback registered")

    def send_command(self, robot_id: str, command: str) -> None:
        """Send command to robot"""
        if robot_id not in self.connections:
            logger.warning(f"[DEBUG][send_command] Robot {robot_id} not found in connections")
            return

        try:
            connection = self.connections[robot_id]
            logger.info(f"[DEBUG][send_command] Called for robot {robot_id}")

            has_dc = hasattr(connection, 'data_channel') and connection.data_channel is not None
            dc_state = connection.data_channel.readyState if has_dc else "NO_DATA_CHANNEL"
            logger.info(f"[DEBUG][send_command] data_channel exists={has_dc}, readyState={dc_state}")

            if has_dc:
                loop = self._get_or_create_event_loop()
                logger.info(
                    f"[DEBUG][send_command] loop={loop}, "
                    f"loop_running={loop.is_running() if loop else None}"
                )

                if dc_state == "open":
                    if loop and loop.is_running():
                        fut = asyncio.run_coroutine_threadsafe(
                            self._async_send_command(connection, command),
                            loop
                        )
                        logger.info(f"[DEBUG][send_command] command scheduled on running loop for robot {robot_id}")
                        # opcional: inspección rápida del resultado sin bloquear demasiado
                        try:
                            fut.result(timeout=1.0)
                            logger.info(f"[DEBUG][send_command] async send completed for robot {robot_id}")
                        except Exception as send_e:
                            logger.error(f"[DEBUG][send_command] async send future raised: {send_e}", exc_info=True)
                    else:
                        logger.warning("[DEBUG][send_command] No running loop available, using synchronous send")
                        connection.data_channel.send(command)
                        logger.info(f"[DEBUG][send_command] command sent synchronously for robot {robot_id}")
                else:
                    logger.warning(f"[DEBUG][send_command] Data channel not open for robot {robot_id}; state={dc_state}")
            else:
                logger.warning(f"[DEBUG][send_command] No data channel available for robot {robot_id}")

        except Exception as e:
            logger.error(f"Error sending command to robot {robot_id}: {e}", exc_info=True)

    def _get_or_create_event_loop(self):
        """Get existing event loop or return the main loop"""
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return self.main_loop

    async def _async_send_command(self, connection, command: str):
        """Async wrapper for sending commands"""
        try:
            if hasattr(connection, 'data_channel') and connection.data_channel:
                logger.info(
                    f"[DEBUG][_async_send_command] Sending command through data channel, "
                    f"state={connection.data_channel.readyState}"
                )
                connection.data_channel.send(command)
                logger.info(f"[DEBUG][_async_send_command] Command sent: {command[:200]}")
            else:
                logger.warning("[DEBUG][_async_send_command] No data channel available")
        except Exception as e:
            logger.error(f"Error in async send command: {e}", exc_info=True)

    def send_movement_command(self, robot_id: str, x: float, y: float, z: float) -> None:
        """Send movement command to robot"""
        try:
            command = gen_mov_command(
                round(x, 2),
                round(y, 2),
                round(z, 2),
                self.config.obstacle_avoidance
            )
            logger.info(
                f"[DEBUG][send_movement_command] robot_id={robot_id}, "
                f"x={x}, y={y}, z={z}, obstacle_avoidance={self.config.obstacle_avoidance}"
            )
            logger.info(f"[DEBUG][send_movement_command] command={command}")
            self.send_command(robot_id, command)
        except Exception as e:
            logger.error(f"Error sending movement command: {e}", exc_info=True)

    def send_stand_up_command(self, robot_id: str) -> None:
        """Send stand up command"""
        try:
            stand_up_cmd = gen_command(ROBOT_CMD["StandUp"])
            logger.info(f"[DEBUG][send_stand_up_command] stand_up_cmd={stand_up_cmd}")
            self.send_command(robot_id, stand_up_cmd)

            move_cmd = gen_command(ROBOT_CMD['BalanceStand'])
            logger.info(f"[DEBUG][send_stand_up_command] balance_stand_cmd={move_cmd}")
            self.send_command(robot_id, move_cmd)
        except Exception as e:
            logger.error(f"Error sending stand up command: {e}", exc_info=True)

    def send_stand_down_command(self, robot_id: str) -> None:
        """Send stand down command"""
        try:
            stand_down_cmd = gen_command(ROBOT_CMD["StandDown"])
            logger.info(f"[DEBUG][send_stand_down_command] stand_down_cmd={stand_down_cmd}")
            self.send_command(robot_id, stand_down_cmd)
        except Exception as e:
            logger.error(f"Error sending stand down command: {e}", exc_info=True)

    def send_webrtc_request(self, robot_id: str, api_id: int, parameter: Any, topic: str) -> None:
        """Send WebRTC request"""
        try:
            payload = gen_command(api_id, parameter, topic)
            logger.info(
                f"[DEBUG][send_webrtc_request] queueing request for robot {robot_id}: "
                f"api_id={api_id}, topic={topic}, payload={payload}"
            )
            self.webrtc_msgs.put_nowait(payload)
        except Exception as e:
            logger.error(f"Error sending WebRTC request: {e}", exc_info=True)

    def process_webrtc_commands(self, robot_id: str) -> None:
        """Process WebRTC commands from queue"""
        processed = 0
        while True:
            try:
                message = self.webrtc_msgs.get_nowait()
                try:
                    logger.info(f"[DEBUG][process_webrtc_commands] Dequeued message for robot {robot_id}: {message}")
                    self.send_command(robot_id, message)
                    processed += 1
                finally:
                    self.webrtc_msgs.task_done()
            except asyncio.QueueEmpty:
                if processed > 0:
                    logger.info(f"[DEBUG][process_webrtc_commands] Processed {processed} queued messages for robot {robot_id}")
                break

    def _on_validated(self, robot_id: str) -> None:
        """Callback after connection validation"""
        try:
            logger.info(f"[DEBUG][_on_validated] Validation callback called for robot {robot_id}")

            if robot_id in self.connections:
                logger.info(f"[DEBUG][_on_validated] Subscribing RTC topics for robot {robot_id}")
                for topic in RTC_TOPIC.values():
                    sub_msg = json.dumps({"type": "subscribe", "topic": topic})
                    logger.info(f"[DEBUG][_on_validated] subscribe -> {sub_msg}")
                    self.connections[robot_id].data_channel.send(sub_msg)
            else:
                logger.warning(f"[DEBUG][_on_validated] robot_id={robot_id} not in connections")

            if self.on_validated_callback:
                self.on_validated_callback(robot_id)

        except Exception as e:
            logger.error(f"Error in validated callback: {e}", exc_info=True)

    def _on_data_channel_message(self, _, msg: Dict[str, Any], robot_id: str) -> None:
        """Handle incoming data channel messages"""
        try:
            logger.info(f"[DEBUG][_on_data_channel_message] Message received from robot {robot_id}")
            if msg is not None:
                logger.info(f"[DEBUG][_on_data_channel_message] Decoded message keys={list(msg.keys()) if isinstance(msg, dict) else type(msg)}")

            if self.data_callback:
                robot_data = RobotData(robot_id=robot_id, timestamp=0.0)
                self.data_callback(msg, robot_id)

        except Exception as e:
            logger.error(f"Error processing data channel message: {e}", exc_info=True)