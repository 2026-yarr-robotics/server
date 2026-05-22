"""Cup detection domain: tracks YOLO-detected cups from /cup_poses topic."""

from __future__ import annotations

import logging
import time
from typing import Any

from ..ros.bridge import RosBridge
from ..ros.launch import LaunchManager

logger = logging.getLogger(__name__)

_CUP_POSES_TOPIC = "/cup_poses"
_CUP_POSES_TYPE = "cup_stack_interfaces/msg/CupPoseArray"


class CupDetectionDomain:
    """Subscribes to /cup_poses and exposes current cup detection state."""

    def __init__(self, bridge: RosBridge, launcher: LaunchManager) -> None:
        self._bridge = bridge
        self._launcher = launcher
        self._latest: dict[str, Any] = {
            "stamp": 0.0,
            "frame_id": "base_link",
            "count": 0,
            "cups": [],
        }
        self._subscribed = False

    def subscribe(self) -> None:
        if self._subscribed:
            return
        self._bridge.subscribe(
            _CUP_POSES_TOPIC,
            _CUP_POSES_TYPE,
            self._on_cup_poses,
        )
        self._subscribed = True
        logger.info("CupDetectionDomain subscribed to %s", _CUP_POSES_TOPIC)

    def _on_cup_poses(self, msg: dict[str, Any]) -> None:
        header = msg.get("header", {})
        stamp_raw = header.get("stamp", {})
        stamp = float(stamp_raw.get("sec", 0)) + float(stamp_raw.get("nanosec", 0)) * 1e-9
        if stamp == 0.0:
            stamp = time.time()

        frame_id = header.get("frame_id", "base_link")
        poses = msg.get("poses", [])

        cups = []
        for pose in poses:
            pos = pose.get("position", {})
            pose_valid = pose.get("pose_valid", False)
            bbox_raw = pose.get("bbox", [0, 0, 0, 0])

            cups.append({
                "id": pose.get("cup_id", ""),
                "label": pose.get("label", "cup"),
                "confidence": float(pose.get("confidence", 0.0)),
                "position": (
                    {"x": float(pos.get("x", 0.0)), "y": float(pos.get("y", 0.0)), "z": float(pos.get("z", 0.0))}
                    if pose_valid else None
                ),
                "pixel": {
                    "x": float(pose.get("cx", 0.0)),
                    "y": float(pose.get("cy", 0.0)),
                },
                "bbox": {
                    "x_min": float(bbox_raw[0]),
                    "y_min": float(bbox_raw[1]),
                    "x_max": float(bbox_raw[2]),
                    "y_max": float(bbox_raw[3]),
                },
            })

        self._latest = {
            "stamp": stamp,
            "frame_id": frame_id,
            "count": len(cups),
            "cups": cups,
        }

    def get_cups(self) -> dict[str, Any]:
        return self._latest

    def is_running(self) -> bool:
        task = self._launcher._tasks.get("cup_detection")
        if task is None:
            return False
        from ..ros.launch import TaskStatus
        return task.status == TaskStatus.RUNNING

    def get_cup_by_id(self, cup_id: str) -> dict[str, Any] | None:
        for cup in self._latest["cups"]:
            if cup["id"] == cup_id:
                return cup
        return None
