"""ROS 2 launch command executor with process tracking."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..config import WorkspaceConfig

logger = logging.getLogger(__name__)

BRINGUP_COMMANDS = {"bringup_sim", "bringup_real"}
TASK_COMMANDS = {
    "cup_pyramid",
    "cup_unstack",
    "cup_pyramid_select",
    "cup_unstack_select",
}
ALL_COMMANDS = BRINGUP_COMMANDS | TASK_COMMANDS


class TaskStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass
class RunningTask:
    name: str
    command: str
    args: dict[str, Any]
    process: asyncio.subprocess.Process
    status: TaskStatus = TaskStatus.RUNNING
    log_lines: list[str] = field(default_factory=list)
    _max_log: int = 500

    def append_log(self, line: str) -> None:
        self.log_lines.append(line)
        if len(self.log_lines) > self._max_log:
            self.log_lines = self.log_lines[-self._max_log :]


class LaunchManager:
    """Tracks and controls ROS 2 launch subprocesses."""

    def __init__(self, workspace: WorkspaceConfig) -> None:
        self._workspace = workspace
        self._tasks: dict[str, RunningTask] = {}
        self._log_futures: dict[str, asyncio.Task[None]] = {}

    @property
    def active_task(self) -> RunningTask | None:
        for task in self._tasks.values():
            if task.status == TaskStatus.RUNNING:
                return task
        return None

    def list_tasks(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "command": t.command,
                "status": t.status.value,
                "pid": t.process.pid if t.process.returncode is None else None,
            }
            for t in self._tasks.values()
        ]

    async def start(
        self,
        command: str,
        args: dict[str, Any] | None = None,
    ) -> RunningTask:
        if command not in ALL_COMMANDS:
            raise ValueError(f"Unknown command: {command}")

        active = self.active_task
        if active is not None:
            raise RuntimeError(
                f"Task '{active.name}' is already running. Stop it first."
            )

        args = args or {}
        cmd = self._build_command(command, args)

        env = os.environ.copy()
        env.setdefault(
            "PATH",
            "/opt/ros/humble/bin:" + env.get("PATH", ""),
        )

        install_setup = str(self._workspace.root / "install" / "setup.bash")
        if Path(install_setup).exists():
            cmd = f"source /opt/ros/humble/setup.bash && source {install_setup} && {cmd}"

        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            preexec_fn=os.setsid,
        )

        task = RunningTask(
            name=command,
            command=command,
            args=args,
            process=process,
        )
        self._tasks[command] = task
        self._log_futures[command] = asyncio.create_task(
            self._read_output(task)
        )
        logger.info("Started task '%s' (pid %d)", command, process.pid)
        return task

    async def stop(self, name: str) -> None:
        task = self._tasks.get(name)
        if task is None:
            raise KeyError(f"No task named '{name}'")

        task.status = TaskStatus.STOPPING
        try:
            pgid = os.getpgid(task.process.pid)
            os.killpg(pgid, signal.SIGINT)
        except ProcessLookupError:
            pass

        try:
            await asyncio.wait_for(task.process.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            try:
                pgid = os.getpgid(task.process.pid)
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await task.process.wait()

        task.status = TaskStatus.IDLE
        fut = self._log_futures.pop(name, None)
        if fut is not None:
            fut.cancel()
        logger.info("Stopped task '%s'", name)

    async def get_log(self, name: str, tail: int = 50) -> list[str]:
        task = self._tasks.get(name)
        if task is None:
            raise KeyError(f"No task named '{name}'")
        return task.log_lines[-tail:]

    async def shutdown_all(self) -> None:
        for name in list(self._tasks.keys()):
            try:
                await self.stop(name)
            except Exception:
                logger.exception("Error stopping task '%s' during shutdown", name)

    def _build_command(self, command: str, args: dict[str, Any]) -> str:
        if command == "bringup_sim":
            script = self._workspace.cup_stack_dir / self._workspace.bringup_sim_script
            return str(script)

        if command == "bringup_real":
            ip = args.get("ip", "192.168.1.100")
            script = self._workspace.cup_stack_dir / self._workspace.bringup_real_script
            return f"{script} {ip}"

        pkg = self._workspace.launch_package
        launch_args = " ".join(f"{k}:={v}" for k, v in args.items())
        return f"ros2 launch {pkg} {command}.launch.py {launch_args}".strip()

    async def _read_output(self, task: RunningTask) -> None:
        if task.process.stdout is None:
            return
        while True:
            line = await task.process.stdout.readline()
            if not line:
                break
            decoded = line.decode(errors="replace").rstrip()
            task.append_log(decoded)
            if task.status == TaskStatus.RUNNING and task.process.returncode is not None:
                task.status = (
                    TaskStatus.FAILED
                    if task.process.returncode != 0
                    else TaskStatus.IDLE
                )
