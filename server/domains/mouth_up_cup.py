"""Mouth-up-cup domain: detection state from /mouth_up_cup/* topics + lifecycle.

Counterpart of :mod:`server.domains.fallen_cup`, intentionally lighter: the
mouth-up perception node publishes a single grasp pose (the orchestrator
consumes ``/mouth_up_cup/grasp_pose`` directly), so there is no multi-cup /
pose2d stream to track.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from ..config import MouthUpCupConfig, MouthUpCupTopics
from ..ros.bridge import RosBridge
from ..ros.launch import LaunchManager, TaskStatus

logger = logging.getLogger(__name__)

DETECT_COMMAND = "mouth_up_cup_detect"

# Detection data older than this is considered stale (node stopped or no cup
# in frame) and reported as None so the dashboard doesn't show a frozen pose.
DETECTION_STALE_SEC = 2.0


def _f(value: Any) -> float | None:
    """Coerce a rosbridge JSON number to float (NaN/Inf serialize as null)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


class MouthUpCupDomain:
    """Subscribes to mouth-up-cup detection topics and tracks the latest state."""

    def __init__(
        self,
        bridge: RosBridge,
        launcher: LaunchManager,
        config: MouthUpCupConfig | None = None,
        topics: MouthUpCupTopics | None = None,
    ) -> None:
        self._bridge = bridge
        self._launcher = launcher
        self._config = config or MouthUpCupConfig()
        self._topics = topics or MouthUpCupTopics()
        self._subscribed = False

        self._grasp_pose: dict[str, Any] | None = None
        self._grasp_pose_ts: float | None = None

    # ── Subscriptions ──────────────────────────────────────────────────────────

    def subscribe(self) -> None:
        if self._subscribed:
            return
        self._bridge.subscribe(
            self._topics.grasp_pose,
            "geometry_msgs/msg/PoseStamped",
            self._on_grasp_pose,
        )
        self._subscribed = True
        logger.info("MouthUpCupDomain subscribed to /mouth_up_cup/* topics")

    def _on_grasp_pose(self, msg: dict[str, Any]) -> None:
        header = msg.get("header", {})
        pose = msg.get("pose", {})
        pos = pose.get("position", {})
        ori = pose.get("orientation", {})
        # rosbridge serializes NaN as null — skip depth-failed frames.
        px, py, pz = _f(pos.get("x")), _f(pos.get("y")), _f(pos.get("z"))
        if px is None or py is None or pz is None:
            return
        self._grasp_pose = {
            "frame_id": header.get("frame_id", ""),
            "position": {"x": px, "y": py, "z": pz},
            "orientation": {
                "x": _f(ori.get("x")) or 0.0,
                "y": _f(ori.get("y")) or 0.0,
                "z": _f(ori.get("z")) or 0.0,
                "w": _f(ori.get("w")) if _f(ori.get("w")) is not None else 1.0,
            },
        }
        self._grasp_pose_ts = time.monotonic()

    # ── Queries ────────────────────────────────────────────────────────────────

    def is_detection_running(self) -> bool:
        task = self._launcher._tasks.get(DETECT_COMMAND)
        if task is None:
            return False
        return task.status == TaskStatus.RUNNING

    def get_state(self) -> dict[str, Any]:
        now = time.monotonic()
        fresh = (
            self._grasp_pose_ts is not None
            and (now - self._grasp_pose_ts) <= DETECTION_STALE_SEC
        )
        grasp = self._grasp_pose if fresh else None
        return {
            "detection_running": self.is_detection_running(),
            "detected": grasp is not None,
            "grasp_pose": grasp,
        }

    # ── Detection lifecycle ────────────────────────────────────────────────────

    def build_detection_args(
        self,
        conf: float | None = None,
        imgsz: int | None = None,
        target_class_name: str | None = None,
        weights_path: str | None = None,
    ) -> dict[str, str]:
        """Merge request overrides with config defaults into launch args."""
        cfg = self._config
        args: dict[str, str] = {
            "conf": str(conf if conf is not None else cfg.conf),
            "imgsz": str(imgsz if imgsz is not None else cfg.imgsz),
            "target_class_name": (
                target_class_name
                if target_class_name is not None
                else cfg.target_class_name
            ),
            "device": cfg.device,
        }
        weights = weights_path or cfg.weights_path
        if weights:
            args["weights_path"] = weights
        return args

    async def start_detection(self, args: dict[str, str]) -> dict[str, Any]:
        task = await self._launcher.start(DETECT_COMMAND, args)
        return {
            "name": task.name,
            "status": task.status.value,
            "pid": task.process.pid if task.process else None,
        }

    async def stop_detection(self) -> dict[str, Any]:
        """Stop the detection node. No robot-motion stop — perception only."""
        await self._launcher.stop(DETECT_COMMAND)
        return {"name": DETECT_COMMAND, "status": "stopped"}
