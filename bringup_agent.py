#!/usr/bin/env python3
"""
Bringup agent — runs on the host PC (not inside Docker).
Controls ROS 2 / MoveIt bringup processes on behalf of the containerised server.
Listens on http://0.0.0.0:8099; Docker reaches it via host.docker.internal:8099.

Endpoints
---------
GET  /health          — liveness check
GET  /status          — {"status": "idle|running|failed", "log": [...last 50 lines...]}
POST /start           — body: {"mode": "real"|"sim", "ip": "192.168.1.100"}
POST /stop            — no body required
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
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

# ── Shared state (protected by _lock) ─────────────────────────────────────────
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


# ── HTTP handler ───────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self._json({"ok": True})
        elif self.path == "/status":
            with _lock:
                data: dict[str, Any] = {
                    "status": _status,
                    "log": list(_log_lines[-50:]),
                }
            self._json(data)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        global _proc, _log_lines, _status

        length = int(self.headers.get("Content-Length", 0))
        body: dict[str, Any] = json.loads(self.rfile.read(length) or b"{}")

        if self.path == "/start":
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
                    script = CUP_STACK_DIR / "bringup_real.sh"
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
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGINT)
                except ProcessLookupError:
                    pass
                with _lock:
                    _status = "idle"
                logger.info("SIGINT sent to bringup (pid=%d)", proc.pid)
                self._json({"status": "stopped"})
            else:
                with _lock:
                    _status = "idle"
                self._json({"status": "not running"})

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


def main() -> None:
    logger.info("cup_stack dir: %s", CUP_STACK_DIR)
    server = HTTPServer(("0.0.0.0", PORT), _Handler)
    logger.info("bringup agent listening on http://0.0.0.0:%d", PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    logger.info("bringup agent stopped")


if __name__ == "__main__":
    main()
