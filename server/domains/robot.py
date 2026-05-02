"""Robot + gripper domain: joint state tracking and task status."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from ..ros.bridge import RosBridge
from ..ros.launch import LaunchManager, TaskStatus

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._bridge = bridge
        self._launcher = launcher
        self._status = RobotStatus()
        self._joint_topic = joint_states_topic
        self._subscribed = False

    @property
    def status(self) -> RobotStatus:
        active = self._launcher.active_task
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
        self._subscribed = True

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
            "pid": task.process.pid,
        }

    async def stop_task(self, name: str) -> dict[str, Any]:
        await self._launcher.stop(name)
        return {"name": name, "status": "stopped"}

    def get_status(self) -> dict[str, Any]:
        s = self.status
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
            "tasks": self._launcher.list_tasks(),
        }

    async def get_log(self, name: str, tail: int = 50) -> list[str]:
        return await self._launcher.get_log(name, tail)
