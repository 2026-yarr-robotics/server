"""Tests for CupDetectionDomain."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from server.domains.cup_detection import CupDetectionDomain, WEB_TASK_COMMANDS
from server.ros.launch import RunningTask, TaskStatus


def _make_running_task(name: str) -> RunningTask:
    process = MagicMock()
    process.pid = 9999
    process.returncode = None
    return RunningTask(name=name, command=name, args={}, process=process, status=TaskStatus.RUNNING)


def _cup_poses_msg(with_cups: bool = True) -> dict:
    msg: dict = {
        "header": {
            "stamp": {"sec": 1715165696, "nanosec": 789000000},
            "frame_id": "base_link",
        },
        "poses": [],
    }
    if with_cups:
        msg["poses"] = [
            {
                "cup_id": "cup_0",
                "label": "cup",
                "confidence": 0.95,
                "position": {"x": 0.35, "y": 0.02, "z": 0.30},
                "cx": 320.0,
                "cy": 240.0,
                "bbox": [300, 220, 340, 260],
                "pose_valid": True,
            },
            {
                "cup_id": "cup_1",
                "label": "cup",
                "confidence": 0.70,
                "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                "cx": 100.0,
                "cy": 200.0,
                "bbox": [80, 180, 120, 220],
                "pose_valid": False,
            },
        ]
    return msg


class TestCupDetectionDomain:
    def test_initial_state_empty(self, cup_detection_domain: CupDetectionDomain):
        cups = cup_detection_domain.get_cups()
        assert cups["count"] == 0
        assert cups["cups"] == []
        assert cups["frame_id"] == "base_link"

    def test_subscribe_calls_bridge(self, cup_detection_domain: CupDetectionDomain):
        cup_detection_domain.subscribe()
        cup_detection_domain._bridge.subscribe.assert_called_once()
        args = cup_detection_domain._bridge.subscribe.call_args
        assert args[0][0] == "/cup_poses"

    def test_subscribe_idempotent(self, cup_detection_domain: CupDetectionDomain):
        cup_detection_domain.subscribe()
        cup_detection_domain.subscribe()
        cup_detection_domain._bridge.subscribe.assert_called_once()

    def test_on_cup_poses_parses_correctly(self, cup_detection_domain: CupDetectionDomain):
        cup_detection_domain._on_cup_poses(_cup_poses_msg())
        cups = cup_detection_domain.get_cups()
        assert cups["count"] == 2
        assert cups["frame_id"] == "base_link"
        assert abs(cups["stamp"] - 1715165696.789) < 0.001

        cup0 = cups["cups"][0]
        assert cup0["id"] == "cup_0"
        assert cup0["confidence"] == 0.95
        assert cup0["position"] is not None
        assert cup0["position"]["x"] == pytest.approx(0.35)
        assert cup0["pixel"]["x"] == 320.0
        assert cup0["bbox"]["x_min"] == 300.0

    def test_pose_invalid_sets_position_none(self, cup_detection_domain: CupDetectionDomain):
        cup_detection_domain._on_cup_poses(_cup_poses_msg())
        cup1 = cup_detection_domain.get_cups()["cups"][1]
        assert cup1["id"] == "cup_1"
        assert cup1["position"] is None

    def test_on_cup_poses_empty(self, cup_detection_domain: CupDetectionDomain):
        cup_detection_domain._on_cup_poses(_cup_poses_msg(with_cups=False))
        cups = cup_detection_domain.get_cups()
        assert cups["count"] == 0

    def test_get_cup_by_id_found(self, cup_detection_domain: CupDetectionDomain):
        cup_detection_domain._on_cup_poses(_cup_poses_msg())
        cup = cup_detection_domain.get_cup_by_id("cup_0")
        assert cup is not None
        assert cup["id"] == "cup_0"

    def test_get_cup_by_id_not_found(self, cup_detection_domain: CupDetectionDomain):
        cup_detection_domain._on_cup_poses(_cup_poses_msg())
        cup = cup_detection_domain.get_cup_by_id("cup_99")
        assert cup is None

    def test_is_running_false_when_no_task(self, cup_detection_domain: CupDetectionDomain):
        cup_detection_domain._launcher._tasks = {}
        assert cup_detection_domain.is_running() is False

    def test_is_running_true_when_task_running(self, cup_detection_domain: CupDetectionDomain):
        task = _make_running_task("cup_detection")
        cup_detection_domain._launcher._tasks = {"cup_detection": task}
        assert cup_detection_domain.is_running() is True

    def test_is_running_false_when_task_not_running(self, cup_detection_domain: CupDetectionDomain):
        task = _make_running_task("cup_detection")
        task.status = TaskStatus.IDLE
        cup_detection_domain._launcher._tasks = {"cup_detection": task}
        assert cup_detection_domain.is_running() is False

    @pytest.mark.asyncio
    async def test_trigger_task_invalid_task(self, cup_detection_domain: CupDetectionDomain):
        with pytest.raises(ValueError, match="task must be one of"):
            await cup_detection_domain.trigger_task("cup_0", "cup_pyramid")

    @pytest.mark.asyncio
    async def test_trigger_task_detection_not_running(self, cup_detection_domain: CupDetectionDomain):
        cup_detection_domain._launcher._tasks = {}
        with pytest.raises(RuntimeError, match="cup_detection task is not running"):
            await cup_detection_domain.trigger_task("cup_0", "cup_pyramid_web")

    @pytest.mark.asyncio
    async def test_trigger_task_cup_not_found(self, cup_detection_domain: CupDetectionDomain):
        task = _make_running_task("cup_detection")
        cup_detection_domain._launcher._tasks = {"cup_detection": task}
        cup_detection_domain._on_cup_poses(_cup_poses_msg())
        with pytest.raises(KeyError, match="cup_99"):
            await cup_detection_domain.trigger_task("cup_99", "cup_pyramid_web")

    @pytest.mark.asyncio
    async def test_trigger_task_success(self, cup_detection_domain: CupDetectionDomain):
        task = _make_running_task("cup_detection")
        cup_detection_domain._launcher._tasks = {"cup_detection": task}
        cup_detection_domain._on_cup_poses(_cup_poses_msg())

        started = _make_running_task("cup_pyramid_web")
        cup_detection_domain._launcher.start = AsyncMock(return_value=started)

        result = await cup_detection_domain.trigger_task("cup_0", "cup_pyramid_web")
        assert result["name"] == "cup_pyramid_web"
        assert result["status"] == "running"

        cup_detection_domain._launcher.start.assert_called_once_with(
            "cup_pyramid_web",
            {"pixel_x": "320", "pixel_y": "240"},
        )


def test_web_task_commands_set():
    assert "cup_pyramid_web" in WEB_TASK_COMMANDS
    assert "cup_unstack_web" in WEB_TASK_COMMANDS
    assert "cup_pyramid" not in WEB_TASK_COMMANDS
