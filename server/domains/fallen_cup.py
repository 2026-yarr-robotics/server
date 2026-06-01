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


def _f(value: Any) -> float | None:
    """Coerce a rosbridge JSON number to float.

    rosbridge serializes NaN/Inf as ``null`` — return None for those (and for
    anything non-numeric) instead of raising.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


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
        raw = msg.get("data", [])
        if len(raw) < _POSE2D_FIELDS:
            return
        data = [_f(v) for v in raw[:_POSE2D_FIELDS]]
        if any(v is None for v in data):
            return  # NaN(→null) 포함 프레임은 무시
        self._pose2d = {
            "top": {"x": data[0], "y": data[1]},
            "bottom": {"x": data[2], "y": data[3]},
            "direction": {"x": data[4], "y": data[5]},
            "yaw": data[6],
            "grip": {"x": data[7], "y": data[8]},
            "confidence": data[9],
            "top_width": data[10],
            "bottom_width": data[11],
        }
        self._pose2d_ts = time.monotonic()

    def _on_grasp_pose(self, msg: dict[str, Any]) -> None:
        header = msg.get("header", {})
        pose = msg.get("pose", {})
        pos = pose.get("position", {})
        ori = pose.get("orientation", {})
        # rosbridge는 NaN을 null로 직렬화 — depth 실패 프레임은 무시
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

    def _on_cups_pose2d(self, msg: dict[str, Any]) -> None:
        data = msg.get("data", [])
        n = len(data) // _CUPS_ROW_FIELDS
        cups = []
        for i in range(n):
            row = [_f(v) for v in data[i * _CUPS_ROW_FIELDS : (i + 1) * _CUPS_ROW_FIELDS]]
            # 핵심 필드(cup_id/yaw/grip/conf)에 NaN(→null)이 있으면 해당 row 스킵
            if any(row[j] is None for j in (0, 7, 8, 9, 10)):
                continue
            cups.append({
                "cup_id": int(row[0]),
                "yaw": row[7],
                "grip_pixel": {"x": row[8], "y": row[9]},
                "confidence": row[10],
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
            # rosbridge는 NaN을 null로 직렬화 → depth 실패 cup은 None
            x, y, z = _f(pos.get("x")), _f(pos.get("y")), _f(pos.get("z"))
            if x is None or y is None or z is None:
                positions.append(None)  # depth unavailable for this cup
            else:
                positions.append({"x": x, "y": y, "z": z})
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
