"""Fallen-cup domain: detection state from /fallen_cup/* topics + lifecycle."""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from ..config import FallenCupConfig, FallenCupTopics
from ..ros.bridge import RosBridge
from ..ros.launch import LaunchManager, TaskStatus

logger = logging.getLogger(__name__)

DETECT_COMMAND = "fallen_cup_detect"

# Detection data older than this is considered stale (node stopped or no cup
# in frame) and reported as None / empty so the dashboard doesn't show a
# frozen pose.
DETECTION_STALE_SEC = 2.0

# /fallen_cup/pose2d field layout (12 floats):
#   top_x, top_y, bottom_x, bottom_y, dir_x, dir_y, yaw,
#   grip_x, grip_y, conf, top_w, bot_w
_POSE2D_FIELDS = 12

# /fallen_cup/cups_pose2d row layout (13 floats per cup):
#   cup_id, top_x, top_y, bot_x, bot_y, dir_x, dir_y, yaw,
#   grip_x, grip_y, conf, top_w, bot_w
_CUPS_ROW_FIELDS = 13


class FallenCupDomain:
    """Subscribes to fallen-cup detection topics and tracks the latest state."""

    def __init__(
        self,
        bridge: RosBridge,
        launcher: LaunchManager,
        config: FallenCupConfig | None = None,
        topics: FallenCupTopics | None = None,
    ) -> None:
        self._bridge = bridge
        self._launcher = launcher
        self._config = config or FallenCupConfig()
        self._topics = topics or FallenCupTopics()
        self._subscribed = False

        self._pose2d: dict[str, Any] | None = None
        self._pose2d_ts: float | None = None
        self._grasp_pose: dict[str, Any] | None = None
        self._grasp_pose_ts: float | None = None
        self._cups: list[dict[str, Any]] = []
        self._cups_ts: float | None = None
        self._cups_grasp: list[dict[str, float] | None] = []

    # ── Subscriptions ──────────────────────────────────────────────────────────

    def subscribe(self) -> None:
        if self._subscribed:
            return
        self._bridge.subscribe(
            self._topics.pose2d,
            "std_msgs/msg/Float32MultiArray",
            self._on_pose2d,
        )
        self._bridge.subscribe(
            self._topics.grasp_pose,
            "geometry_msgs/msg/PoseStamped",
            self._on_grasp_pose,
        )
        self._bridge.subscribe(
            self._topics.cups_pose2d,
            "std_msgs/msg/Float32MultiArray",
            self._on_cups_pose2d,
        )
        self._bridge.subscribe(
            self._topics.cups_grasp_poses,
            "geometry_msgs/msg/PoseArray",
            self._on_cups_grasp_poses,
        )
        self._subscribed = True
        logger.info("FallenCupDomain subscribed to /fallen_cup/* topics")

    def _on_pose2d(self, msg: dict[str, Any]) -> None:
        data = msg.get("data", [])
        if len(data) < _POSE2D_FIELDS:
            return
        self._pose2d = {
            "top": {"x": float(data[0]), "y": float(data[1])},
            "bottom": {"x": float(data[2]), "y": float(data[3])},
            "direction": {"x": float(data[4]), "y": float(data[5])},
            "yaw": float(data[6]),
            "grip": {"x": float(data[7]), "y": float(data[8])},
            "confidence": float(data[9]),
            "top_width": float(data[10]),
            "bottom_width": float(data[11]),
        }
        self._pose2d_ts = time.monotonic()

    def _on_grasp_pose(self, msg: dict[str, Any]) -> None:
        header = msg.get("header", {})
        pose = msg.get("pose", {})
        pos = pose.get("position", {})
        ori = pose.get("orientation", {})
        self._grasp_pose = {
            "frame_id": header.get("frame_id", ""),
            "position": {
                "x": float(pos.get("x", 0.0)),
                "y": float(pos.get("y", 0.0)),
                "z": float(pos.get("z", 0.0)),
            },
            "orientation": {
                "x": float(ori.get("x", 0.0)),
                "y": float(ori.get("y", 0.0)),
                "z": float(ori.get("z", 0.0)),
                "w": float(ori.get("w", 1.0)),
            },
        }
        self._grasp_pose_ts = time.monotonic()

    def _on_cups_pose2d(self, msg: dict[str, Any]) -> None:
        data = msg.get("data", [])
        n = len(data) // _CUPS_ROW_FIELDS
        cups = []
        for i in range(n):
            row = data[i * _CUPS_ROW_FIELDS : (i + 1) * _CUPS_ROW_FIELDS]
            cups.append({
                "cup_id": int(row[0]),
                "yaw": float(row[7]),
                "grip_pixel": {"x": float(row[8]), "y": float(row[9])},
                "confidence": float(row[10]),
                "position": None,  # filled from cups_grasp_poses by index
            })
        # Join with the latest PoseArray by index (the node publishes both
        # in the same callback, so lengths normally match).
        for i, cup in enumerate(cups):
            if i < len(self._cups_grasp):
                cup["position"] = self._cups_grasp[i]
        self._cups = cups
        self._cups_ts = time.monotonic()

    def _on_cups_grasp_poses(self, msg: dict[str, Any]) -> None:
        positions: list[dict[str, float] | None] = []
        for pose in msg.get("poses", []):
            pos = pose.get("position", {})
            x = float(pos.get("x", float("nan")))
            if math.isnan(x):
                positions.append(None)  # depth unavailable for this cup
            else:
                positions.append({
                    "x": x,
                    "y": float(pos.get("y", 0.0)),
                    "z": float(pos.get("z", 0.0)),
                })
        self._cups_grasp = positions

    # ── Queries ────────────────────────────────────────────────────────────────

    def is_detection_running(self) -> bool:
        task = self._launcher._tasks.get(DETECT_COMMAND)
        if task is None:
            return False
        return task.status == TaskStatus.RUNNING

    def get_state(self) -> dict[str, Any]:
        now = time.monotonic()

        def fresh(ts: float | None) -> bool:
            return ts is not None and (now - ts) <= DETECTION_STALE_SEC

        cups = self._cups if fresh(self._cups_ts) else []
        return {
            "detection_running": self.is_detection_running(),
            "count": len(cups),
            "cups": cups,
            "pose2d": self._pose2d if fresh(self._pose2d_ts) else None,
            "grasp_pose": self._grasp_pose if fresh(self._grasp_pose_ts) else None,
        }

    # ── Detection lifecycle ────────────────────────────────────────────────────

    def build_detection_args(
        self,
        conf: float | None = None,
        imgsz: int | None = None,
        use_depth: bool | None = None,
        weights_path: str | None = None,
    ) -> dict[str, str]:
        """Merge request overrides with config defaults into launch args."""
        cfg = self._config
        args: dict[str, str] = {
            "conf": str(conf if conf is not None else cfg.conf),
            "imgsz": str(imgsz if imgsz is not None else cfg.imgsz),
            "use_depth": str(
                use_depth if use_depth is not None else cfg.use_depth
            ).lower(),
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
