"""ROS 2 launch command executor with process tracking."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import urllib.request
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
    "cup_pyramid_web",
    "cup_unstack_web",
    "move_cartesian",
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
    process: asyncio.subprocess.Process | None  # None when delegated to bringup agent
    status: TaskStatus = TaskStatus.RUNNING
    log_lines: list[str] = field(default_factory=list)
    _max_log: int = 500

    def append_log(self, line: str) -> None:
        self.log_lines.append(line)
        if len(self.log_lines) > self._max_log:
            self.log_lines = self.log_lines[-self._max_log :]


class LaunchManager:
    """Tracks and controls ROS 2 launch subprocesses.

    Bringup commands (bringup_sim / bringup_real) are delegated to the host
    bringup agent when *agent_url* is set.  Action tasks (cup_pyramid, etc.)
    always run as local subprocesses.
    """

    def __init__(
        self,
        workspace: WorkspaceConfig,
        agent_url: str | None = None,
    ) -> None:
        self._workspace = workspace
        self._agent_url = agent_url
        self._tasks: dict[str, RunningTask] = {}        # action tasks only
        self._bringup: RunningTask | None = None        # at most one bringup
        self._log_futures: dict[str, asyncio.Task[None]] = {}

    # ── Public properties ──────────────────────────────────────────────────────

    @property
    def bringup_task(self) -> RunningTask | None:
        return self._bringup

    @property
    def active_action_task(self) -> RunningTask | None:
        for task in self._tasks.values():
            if task.status == TaskStatus.RUNNING:
                return task
        return None

    @property
    def active_task(self) -> RunningTask | None:
        """Active action task; falls back to bringup when no action is running."""
        action = self.active_action_task
        if action:
            return action
        if self._bringup and self._bringup.status == TaskStatus.RUNNING:
            return self._bringup
        return None

    # ── Start ─────────────────────────────────────────────────────────────────

    async def start(
        self,
        command: str,
        args: dict[str, Any] | None = None,
    ) -> RunningTask:
        if command not in ALL_COMMANDS:
            raise ValueError(f"Unknown command: {command}")

        args = args or {}

        if command in BRINGUP_COMMANDS:
            return await self._start_bringup(command, args)

        active = self.active_action_task
        if active is not None:
            raise RuntimeError(
                f"Task '{active.name}' is already running. Stop it first."
            )

        if self._agent_url:
            return await self._start_task_via_agent(command, args)
        return await self._start_local(command, args)

    async def _start_bringup(self, command: str, args: dict[str, Any]) -> RunningTask:
        if self._bringup and self._bringup.status == TaskStatus.RUNNING:
            raise RuntimeError(f"Bringup '{self._bringup.name}' is already running.")

        if self._agent_url:
            return await self._start_bringup_via_agent(command, args)
        return await self._start_local(command, args, is_bringup=True)

    async def _start_bringup_via_agent(
        self, command: str, args: dict[str, Any]
    ) -> RunningTask:
        mode = "sim" if command == "bringup_sim" else "real"
        payload = {"mode": mode, "ip": args.get("ip", "192.168.1.100")}

        url = f"{self._agent_url}/start"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        loop = asyncio.get_running_loop()

        def _call() -> dict[str, Any]:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())

        resp = await loop.run_in_executor(None, _call)
        logger.info("bringup agent response: %s", resp)

        task = RunningTask(
            name=command,
            command=command,
            args=args,
            process=None,
            status=TaskStatus.RUNNING,
        )
        self._bringup = task
        self._log_futures[command] = asyncio.create_task(
            self._poll_agent(task)
        )
        return task

    async def _start_task_via_agent(
        self, command: str, args: dict[str, Any]
    ) -> RunningTask:
        url = f"{self._agent_url}/task/start"
        payload = {"command": command, "args": args}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        loop = asyncio.get_running_loop()

        def _call() -> dict[str, Any]:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())

        resp = await loop.run_in_executor(None, _call)
        logger.info("task agent response for '%s': %s", command, resp)

        task = RunningTask(
            name=command,
            command=command,
            args=args,
            process=None,
            status=TaskStatus.RUNNING,
        )
        self._tasks[command] = task
        self._log_futures[command] = asyncio.create_task(
            self._poll_task_agent(task)
        )
        return task

    async def _poll_task_agent(self, task: RunningTask) -> None:
        url = f"{self._agent_url}/task/status?name={task.name}"
        loop = asyncio.get_running_loop()

        def _fetch() -> dict[str, Any]:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return json.loads(resp.read())

        while True:
            await asyncio.sleep(1.0)
            try:
                data = await loop.run_in_executor(None, _fetch)
                task.log_lines = data.get("log", [])
                agent_st = data.get("status", "idle")
                if agent_st == "running":
                    task.status = TaskStatus.RUNNING
                elif agent_st == "failed":
                    task.status = TaskStatus.FAILED
                    break
                elif agent_st == "idle" and task.status == TaskStatus.RUNNING:
                    task.status = TaskStatus.IDLE
                    break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Task agent poll error for '%s': %s", task.name, exc)

    async def _stop_task_via_agent(self, command: str) -> None:
        url = f"{self._agent_url}/task/stop"
        payload = {"command": command}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        loop = asyncio.get_running_loop()

        def _call() -> dict[str, Any]:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())

        try:
            await loop.run_in_executor(None, _call)
        except Exception as exc:
            logger.warning("Task agent stop error for '%s': %s", command, exc)

    async def _start_local(
        self,
        command: str,
        args: dict[str, Any],
        is_bringup: bool = False,
    ) -> RunningTask:
        cmd = self._build_command(command, args)

        env = os.environ.copy()
        env.setdefault("PATH", "/opt/ros/humble/bin:" + env.get("PATH", ""))

        install_setup = str(self._workspace.root / "install" / "setup.bash")
        if Path(install_setup).exists():
            cmd = (
                f"source /opt/ros/humble/setup.bash && "
                f"source {install_setup} && {cmd}"
            )

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
        if is_bringup:
            self._bringup = task
        else:
            self._tasks[command] = task

        self._log_futures[command] = asyncio.create_task(
            self._read_output(task)
        )
        logger.info("Started task '%s' (pid %d)", command, process.pid)
        return task

    # ── Stop ──────────────────────────────────────────────────────────────────

    async def stop(self, name: str) -> None:
        if name in BRINGUP_COMMANDS:
            await self._stop_bringup(name)
            return

        task = self._tasks.get(name)
        if task is None:
            raise KeyError(f"No task named '{name}'")

        if self._agent_url and task.process is None:
            await self._stop_task_via_agent(name)
            task.status = TaskStatus.IDLE
            fut = self._log_futures.pop(name, None)
            if fut:
                fut.cancel()
        else:
            await self._stop_local(task)
        logger.info("Stopped task '%s'", name)

    async def _stop_bringup(self, name: str) -> None:
        task = self._bringup
        if task is None or task.name != name:
            raise KeyError(f"No bringup task named '{name}'")

        task.status = TaskStatus.STOPPING

        if self._agent_url and task.process is None:
            await self._stop_via_agent()
        elif task.process is not None:
            await self._stop_local(task)

        task.status = TaskStatus.IDLE
        fut = self._log_futures.pop(name, None)
        if fut:
            fut.cancel()
        logger.info("Stopped bringup '%s'", name)

    async def _stop_local(self, task: RunningTask) -> None:
        task.status = TaskStatus.STOPPING
        if task.process is None:
            return
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
        fut = self._log_futures.pop(task.name, None)
        if fut:
            fut.cancel()

    async def _stop_via_agent(self) -> None:
        url = f"{self._agent_url}/stop"
        req = urllib.request.Request(
            url,
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        loop = asyncio.get_running_loop()

        def _call() -> dict[str, Any]:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())

        try:
            await loop.run_in_executor(None, _call)
        except Exception as exc:
            logger.warning("Agent stop error: %s", exc)

    # ── Queries ───────────────────────────────────────────────────────────────

    def list_tasks(self) -> list[dict[str, Any]]:
        rows = []
        if self._bringup is not None:
            rows.append({
                "name": self._bringup.name,
                "command": self._bringup.command,
                "status": self._bringup.status.value,
                "pid": (
                    self._bringup.process.pid
                    if self._bringup.process and self._bringup.process.returncode is None
                    else None
                ),
            })
        for t in self._tasks.values():
            rows.append({
                "name": t.name,
                "command": t.command,
                "status": t.status.value,
                "pid": t.process.pid if t.process and t.process.returncode is None else None,
            })
        return rows

    async def get_log(self, name: str, tail: int = 50) -> list[str]:
        if name in BRINGUP_COMMANDS:
            if self._bringup and self._bringup.name == name:
                return self._bringup.log_lines[-tail:]
            raise KeyError(f"No bringup task named '{name}'")
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
        if self._bringup and self._bringup.status == TaskStatus.RUNNING:
            try:
                await self._stop_bringup(self._bringup.name)
            except Exception:
                logger.exception("Error stopping bringup during shutdown")

    # ── Internal helpers ───────────────────────────────────────────────────────

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
        if task.process is None or task.process.stdout is None:
            return
        while True:
            line = await task.process.stdout.readline()
            if not line:
                break
            decoded = line.decode(errors="replace").rstrip()
            task.append_log(decoded)
        if task.status == TaskStatus.RUNNING:
            rc = task.process.returncode
            task.status = TaskStatus.IDLE if rc == 0 else TaskStatus.FAILED

    async def _poll_agent(self, task: RunningTask) -> None:
        """Sync bringup status and logs from the agent every second."""
        url = f"{self._agent_url}/status"
        loop = asyncio.get_running_loop()

        def _fetch() -> dict[str, Any]:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return json.loads(resp.read())

        while True:
            await asyncio.sleep(1.0)
            try:
                data = await loop.run_in_executor(None, _fetch)
                task.log_lines = data.get("log", [])
                agent_st = data.get("status", "idle")
                if agent_st == "running":
                    task.status = TaskStatus.RUNNING
                elif agent_st == "failed":
                    task.status = TaskStatus.FAILED
                    break
                elif agent_st == "idle" and task.status == TaskStatus.RUNNING:
                    task.status = TaskStatus.IDLE
                    break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Agent poll error: %s", exc)
