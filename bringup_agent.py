#!/usr/bin/env python3
"""
Bringup agent — runs on the host PC (not inside Docker).
Controls ROS 2 / MoveIt bringup processes on behalf of the containerised server.
Listens on http://0.0.0.0:8099; Docker reaches it via host.docker.internal:8099.

Endpoints
---------
GET  /health          — liveness check
GET  /status          — {"status": "idle|running|failed", "log": [...last 50 lines...], "external": bool}
POST /start           — body: {"mode": "real"|"sim", "ip": "192.168.1.100"}
POST /stop            — no body required
"""
from __future__ import annotations

import json
import logging
import os
import signal
import socketserver
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

PORT = 8099
_SCRIPT_DIR = Path(__file__).resolve().parent
CUP_STACK_DIR = _SCRIPT_DIR.parent / "ros2-cup-stack" / "ros2" / "src" / "cup_stack"
ROS2_WORKSPACE = CUP_STACK_DIR.parent.parent  # ros2-cup-stack/ros2/

# Pattern matching the host bringup launch process. Used by /status to
# surface externally-started bringup (e.g. bringup_real.sh run from a
# host shell) so the dashboard reflects reality regardless of who
# started it.
_BRINGUP_PROCESS_PATTERN = r"dsr_bringup2.*\.launch\.py"


def _external_bringup_running() -> bool:
    """True if a host bringup launch process is alive (pgrep)."""
    try:
        return subprocess.run(
            ["pgrep", "-f", _BRINGUP_PROCESS_PATTERN],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=2,
        ).returncode == 0
    except Exception:
        return False


# ── Bringup state (protected by _lock) ────────────────────────────────────────
_lock = threading.Lock()
_proc: subprocess.Popen[bytes] | None = None
_log_lines: list[str] = []
_status = "idle"  # idle | running | failed


def _read_output(proc: subprocess.Popen[bytes]) -> None:
    global _status
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.decode(errors="replace").rstrip()
        with _lock:
            _log_lines.append(line)
            if len(_log_lines) > 500:
                del _log_lines[:-500]
    with _lock:
        rc = proc.poll()
        if _status == "running":
            _status = "idle" if rc == 0 else "failed"
    logger.info("bringup process exited (rc=%s)", proc.returncode)


# ── Task state (per-command processes) ────────────────────────────────────────
_tasks_lock = threading.Lock()
_task_procs: dict[str, subprocess.Popen[bytes]] = {}
_task_logs: dict[str, list[str]] = {}
_task_statuses: dict[str, str] = {}  # idle | running | failed


def _build_task_cmd(command: str, args: dict[str, str]) -> list[str]:
    # colcon builds the cup_stack overlay at the ros2-cup-stack root
    # (ros2-cup-stack/install), NOT under ros2/. The old ROS2_WORKSPACE/install
    # (= ros2-cup-stack/ros2/install) never existed, so the overlay was never
    # sourced and `ros2 launch cup_stack ...` died with
    # "Package 'cup_stack' not found".
    install_setup = ROS2_WORKSPACE.parent / "install" / "setup.bash"  # ros2-cup-stack/install
    doosan_setup = Path.home() / "ros2_ws" / "install" / "setup.bash"
    moveit_setup = Path.home() / "ws_moveit" / "install" / "setup.bash"
    ros_setup = "/opt/ros/humble/setup.bash"
    launch_args = " ".join(f"{k}:={v}" for k, v in args.items())
    ros_cmd = f"ros2 launch cup_stack {command}.launch.py {launch_args}".strip()
    full = f"source {ros_setup}"
    if moveit_setup.exists():
        full += f" && source {moveit_setup}"
    if doosan_setup.exists():
        full += f" && source {doosan_setup}"
    if install_setup.exists():
        full += f" && source {install_setup}"
    full += f" && {ros_cmd}"
    return ["bash", "-c", full]


def _read_task_output(command: str, proc: subprocess.Popen[bytes]) -> None:
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.decode(errors="replace").rstrip()
        with _tasks_lock:
            if command in _task_logs:
                _task_logs[command].append(line)
                if len(_task_logs[command]) > 500:
                    del _task_logs[command][:-500]
    with _tasks_lock:
        rc = proc.poll()
        if _task_statuses.get(command) == "running":
            _task_statuses[command] = "idle" if rc == 0 else "failed"
    logger.info("task '%s' exited (rc=%s)", command, proc.returncode)


# ── HTTP handler ───────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path, _, qs = self.path.partition("?")
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)

        if path == "/health":
            self._json({"ok": True})
        elif path == "/status":
            with _lock:
                st = _status
                log_snap = list(_log_lines[-50:])
            # If this agent didn't start bringup, surface CLI-launched
            # bringup so the dashboard sees the real running state and
            # the stop button maps to a real kill target.
            external = False
            if st == "idle" and _external_bringup_running():
                st = "running"
                external = True
            self._json({"status": st, "log": log_snap, "external": external})
        elif path == "/task/status":
            name = params.get("name", "")
            if not name:
                self._json({"error": "missing name"}, 400)
                return
            with _tasks_lock:
                status = _task_statuses.get(name, "idle")
                log = list(_task_logs.get(name, [])[-50:])
            self._json({"status": status, "log": log})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        global _proc, _log_lines, _status

        length = int(self.headers.get("Content-Length", 0))
        body: dict[str, Any] = json.loads(self.rfile.read(length) or b"{}")

        if self.path == "/task/start":
            command = body.get("command", "").strip()
            args: dict[str, str] = body.get("args", {})
            if not command:
                self._json({"error": "missing command"}, 400)
                return

            with _tasks_lock:
                existing = _task_procs.get(command)
                if existing is not None and existing.poll() is None:
                    self._json({"error": f"task '{command}' already running"}, 409)
                    return

                cmd = _build_task_cmd(command, args)
                _task_logs[command] = []
                _task_statuses[command] = "running"
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    preexec_fn=os.setsid,
                )
                _task_procs[command] = proc

            t = threading.Thread(target=_read_task_output, args=(command, proc), daemon=True)
            t.start()
            logger.info("task '%s' started (pid=%d)", command, proc.pid)
            self._json({"status": "started", "pid": proc.pid})

        elif self.path == "/task/stop":
            command = body.get("command", "").strip()
            if not command:
                self._json({"error": "missing command"}, 400)
                return

            with _tasks_lock:
                proc = _task_procs.get(command)

            if proc is not None and proc.poll() is None:
                logger.info("stopping task '%s' (pid=%d)…", command, proc.pid)
                _kill_proc(proc)
                logger.info("task '%s' stopped (rc=%s)", command, proc.poll())

            with _tasks_lock:
                _task_statuses[command] = "idle"
            self._json({"status": "stopped"})

        elif self.path == "/start":
            with _lock:
                if _proc is not None and _proc.poll() is None:
                    self._json({"error": "already running", "status": _status}, 409)
                    return

                mode = body.get("mode", "real")
                ip = body.get("ip", "192.168.1.100")

                if mode == "sim":
                    script = CUP_STACK_DIR / "bringup_sim.sh"
                    cmd = ["bash", str(script)]
                else:
                    # Real bringup runs ONLY via the canonical server-side
                    # script (server/bringup_real.sh), never the ROS 2 copy.
                    script = _SCRIPT_DIR / "bringup_real.sh"
                    cmd = ["bash", str(script), ip]

                _log_lines = []
                _status = "running"
                _proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    preexec_fn=os.setsid,
                )

            t = threading.Thread(target=_read_output, args=(_proc,), daemon=True)
            t.start()
            logger.info("bringup started (mode=%s pid=%d)", mode, _proc.pid)
            self._json({"status": "started", "pid": _proc.pid})

        elif self.path == "/stop":
            with _lock:
                proc = _proc

            if proc is not None and proc.poll() is None:
                logger.info("stopping bringup (pid=%d)…", proc.pid)
                _kill_proc(proc)
                logger.info("bringup stopped (rc=%s)", proc.poll())

            # Also stop bringup NOT started by this agent (started
            # externally, or before an agent restart) and reap orphan
            # nodes so a subsequent /start works.
            _force_stop_bringup()

            with _lock:
                _proc = None
                _status = "idle"
            self._json({"status": "stopped"})

        else:
            self._json({"error": "not found"}, 404)

    def _json(self, data: dict[str, Any], code: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:  # silence per-request noise
        pass


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _kill_proc(proc: subprocess.Popen, timeout_sigint: float = 10.0) -> None:
    """Send SIGINT; wait for graceful exit; escalate to SIGKILL if needed."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=timeout_sigint)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


_DSR01_ORPHANS = (
    r"ros2_control_node.*__ns:=/dsr01",
    r"robot_state_publisher.*__ns:=/dsr01",
    r"controller_manager/spawner.*__ns:=/dsr01",
    r"rviz2 .*__ns:=/dsr01",
)


def _pkill(pattern: str, sig: str) -> None:
    subprocess.run(
        ["pkill", sig, "-f", pattern],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _force_stop_bringup() -> None:
    """Stop bringup regardless of who started it.

    Mirrors stop.sh's bringup-kill block (SIGINT→SIGKILL on
    ``dsr_bringup2``) and additionally reaps the orphaned ``/dsr01``
    child nodes a launch-wrapper kill leaves behind — otherwise a
    subsequent /start has multiple controller_manager instances
    contending for the single Doosan DRFL session and /dsr01/motion/*
    calls hang.
    """
    _pkill("dsr_bringup2", "-INT")
    time.sleep(2)
    _pkill("dsr_bringup2", "-KILL")
    for pat in _DSR01_ORPHANS:
        _pkill(pat, "-INT")
    time.sleep(1)
    _pkill(r"ros2_control_node.*__ns:=/dsr01", "-KILL")
    _pkill(r"robot_state_publisher.*__ns:=/dsr01", "-KILL")


def main() -> None:
    logger.info("cup_stack dir: %s", CUP_STACK_DIR)
    server = _ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    logger.info("bringup agent listening on http://0.0.0.0:%d", PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    logger.info("bringup agent stopped")


if __name__ == "__main__":
    main()
