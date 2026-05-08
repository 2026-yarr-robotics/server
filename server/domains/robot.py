"""Robot + gripper domain: joint state tracking and task status."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from ..config import WorkspaceLimits
from ..ros.bridge import RosBridge
from ..ros.launch import BRINGUP_COMMANDS, LaunchManager, TaskStatus

logger = logging.getLogger(__name__)


@dataclass
class MoveLimits:
    """Current move limits from workspace config."""
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float
    grid_spacing: float


@dataclass
class JointState:
    name: list[str] = field(default_factory=list)
    position: list[float] = field(default_factory=list)
    velocity: list[float] = field(default_factory=list)
    effort: list[float] = field(default_factory=list)


@dataclass
class RobotStatus:
    joints: JointState = field(default_factory=JointState)
    task_name: str | None = None
    task_status: TaskStatus = TaskStatus.IDLE


class RobotDomain:
    """Tracks robot state and delegates launch commands."""

    def __init__(
        self,
        bridge: RosBridge,
        launcher: LaunchManager,
        joint_states_topic: str = "/joint_states",
        workspace_limits: WorkspaceLimits | None = None,
    ) -> None:
        self._bridge = bridge
        self._launcher = launcher
        self._status = RobotStatus()
        self._joint_topic = joint_states_topic
        self._subscribed = False
        self._commanded_pos: dict[str, float] | None = None
        self._ee_pos_ros: dict[str, float] | None = None
        self._move_limits = MoveLimits(
            x_min=workspace_limits.x_min if workspace_limits else -0.5,
            x_max=workspace_limits.x_max if workspace_limits else 0.5,
            y_min=workspace_limits.y_min if workspace_limits else -0.5,
            y_max=workspace_limits.y_max if workspace_limits else 0.5,
            z_min=workspace_limits.z_min if workspace_limits else 0.25,
            z_max=workspace_limits.z_max if workspace_limits else 0.55,
            grid_spacing=workspace_limits.grid_spacing if workspace_limits else 0.05,
        )

    @property
    def move_limits(self) -> dict[str, Any]:
        return {
            "x_min": self._move_limits.x_min,
            "x_max": self._move_limits.x_max,
            "y_min": self._move_limits.y_min,
            "y_max": self._move_limits.y_max,
            "z_min": self._move_limits.z_min,
            "z_max": self._move_limits.z_max,
            "grid_spacing": self._move_limits.grid_spacing,
        }

    @property
    def status(self) -> RobotStatus:
        active = self._launcher.active_action_task
        if active is not None:
            self._status.task_name = active.name
            self._status.task_status = active.status
        else:
            self._status.task_name = None
            self._status.task_status = TaskStatus.IDLE
        return self._status

    def subscribe(self) -> None:
        if self._subscribed:
            return
        self._bridge.subscribe(
            self._joint_topic,
            "sensor_msgs/msg/JointState",
            self._on_joint_state,
            throttle_rate=100,
        )
        self._bridge.subscribe(
            "/ee_pose",
            "geometry_msgs/msg/PoseStamped",
            self._on_ee_pose,
            throttle_rate=200,
        )
        self._subscribed = True

    def _on_ee_pose(self, msg: dict[str, Any]) -> None:
        pos = msg.get("pose", {}).get("position", {})
        if pos:
            self._ee_pos_ros = {
                "x": float(pos.get("x", 0.0)),
                "y": float(pos.get("y", 0.0)),
                "z": float(pos.get("z", 0.0)),
            }

    def _on_joint_state(self, msg: dict[str, Any]) -> None:
        self._status.joints = JointState(
            name=msg.get("name", []),
            position=msg.get("position", []),
            velocity=msg.get("velocity", []),
            effort=msg.get("effort", []),
        )

    async def start_task(
        self,
        command: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = await self._launcher.start(command, args)
        return {
            "name": task.name,
            "status": task.status.value,
            "pid": task.process.pid if task.process else None,
        }

    async def stop_task(self, name: str) -> dict[str, Any]:
        await self._launcher.stop(name)
        return {"name": name, "status": "stopped"}

    def get_ee_position(self) -> dict[str, float] | None:
        return self._ee_pos_ros if self._ee_pos_ros is not None else self._commanded_pos

    def get_status(self) -> dict[str, Any]:
        s = self.status
        bringup = self._launcher.bringup_task
        ee_pos = self._ee_pos_ros if self._ee_pos_ros is not None else self._commanded_pos
        return {
            "joints": {
                "name": s.joints.name,
                "position": s.joints.position,
                "velocity": s.joints.velocity,
                "effort": s.joints.effort,
            },
            "task": {
                "name": s.task_name,
                "status": s.task_status.value,
            },
            "bringup": {
                "name": bringup.name if bringup else None,
                "status": bringup.status.value if bringup else "idle",
            },
            "tasks": self._launcher.list_tasks(),
            "ee_position": ee_pos,
        }

    async def gripper_control(self, command: str) -> dict[str, Any]:
        result = await self._bridge.call_service(
            "/gripper_control",
            "cup_stack_interfaces/srv/GripperControl",
            {"command": command},
        )
        return result if result else {"success": False, "message": "Service call failed"}

    async def get_log(self, name: str, tail: int = 50) -> list[str]:
        return await self._launcher.get_log(name, tail)

    def _clamp_coord(self, value: float, min_val: float, max_val: float) -> float:
        """Clamp coordinate within limits."""
        return max(min_val, min(max_val, value))

    def _validate_target(
        self,
        x: float,
        y: float,
        z: float,
    ) -> tuple[float, float, float] | None:
        """Validate and clamp target within workspace limits."""
        if (x < self._move_limits.x_min or x > self._move_limits.x_max or
            y < self._move_limits.y_min or y > self._move_limits.y_max or
            z < self._move_limits.z_min or z > self._move_limits.z_max):
            logger.warning(
                "Target out of bounds: (%.3f, %.3f, %.3f), clamping to workspace",
                x, y, z,
            )
        return (
            self._clamp_coord(x, self._move_limits.x_min, self._move_limits.x_max),
            self._clamp_coord(y, self._move_limits.y_min, self._move_limits.y_max),
            self._clamp_coord(z, self._move_limits.z_min, self._move_limits.z_max),
        )

    async def move_to(
        self,
        x: float,
        y: float,
        z: float,
        mode: str = "absolute",
    ) -> dict[str, Any]:
        """Move robot end-effector to specified position."""
        clamped = self._validate_target(x, y, z)
        if clamped is None:
            raise ValueError("Invalid target position")

        target_x, target_y, target_z = clamped

        result = await self._bridge.call_service(
            "/move_cartesian",
            "cup_stack_interfaces/srv/MoveCartesian",
            {"x": target_x, "y": target_y, "z": target_z, "mode": mode},
        )
        base = result if result else {"success": False, "message": "Service call failed"}

        if base.get("success"):
            self._commanded_pos = {"x": target_x, "y": target_y, "z": target_z}

        return {**base, "position": self._commanded_pos}
