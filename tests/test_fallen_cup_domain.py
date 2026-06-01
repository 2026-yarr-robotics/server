"""Tests for FallenCupDomain."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from server.domains.fallen_cup import DETECT_COMMAND, FallenCupDomain
from server.ros.launch import RunningTask, TaskStatus


def _make_running_task(name: str) -> RunningTask:
    process = MagicMock()
    process.pid = 9999
    process.returncode = None
    return RunningTask(name=name, command=name, args={}, process=process, status=TaskStatus.RUNNING)


def _pose2d_msg() -> dict:
    # top_xy, bottom_xy, dir_xy, yaw, grip_xy, conf, top_w, bot_w
    return {"data": [412.0, 305.5, 520.0, 310.0, -0.99, -0.04, 3.10, 425.0, 306.0, 0.91, 48.0, 72.0]}


def _grasp_pose_msg() -> dict:
    return {
        "header": {"frame_id": "camera_color_optical_frame"},
        "pose": {
            "position": {"x": 0.012, "y": -0.034, "z": 0.41},
            "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
        },
    }


def _cups_pose2d_msg(n: int = 2) -> dict:
    # row: cup_id, top_xy, bot_xy, dir_xy, yaw, grip_xy, conf, top_w, bot_w
    rows = []
    for i in range(n):
        rows.extend([
            float(i),                  # cup_id
            412.0, 305.5,              # top
            520.0, 310.0,              # bottom
            -0.99, -0.04,              # dir
            3.10,                      # yaw
            425.0 + i, 306.0,          # grip
            0.91,                      # conf
            48.0, 72.0,                # widths
        ])
    return {"data": rows}


def _cups_grasp_poses_msg() -> dict:
    return {
        "poses": [
            {"position": {"x": 0.012, "y": -0.034, "z": 0.41}},
            {"position": {"x": float("nan"), "y": 0.0, "z": 0.0}},  # depth 실패
        ],
    }


class TestFallenCupDomain:
    def test_initial_state(self, fallen_cup_domain: FallenCupDomain):
        state = fallen_cup_domain.get_state()
        assert state["detection_running"] is False
        assert state["count"] == 0
        assert state["cups"] == []
        assert state["pose2d"] is None
        assert state["grasp_pose"] is None

    def test_subscribe_calls_bridge_for_all_topics(self, fallen_cup_domain: FallenCupDomain):
        fallen_cup_domain.subscribe()
        topics = [c[0][0] for c in fallen_cup_domain._bridge.subscribe.call_args_list]
        assert "/fallen_cup/pose2d" in topics
        assert "/fallen_cup/grasp_pose" in topics
        assert "/fallen_cup/cups_pose2d" in topics
        assert "/fallen_cup/cups_grasp_poses" in topics

    def test_subscribe_idempotent(self, fallen_cup_domain: FallenCupDomain):
        fallen_cup_domain.subscribe()
        count = fallen_cup_domain._bridge.subscribe.call_count
        fallen_cup_domain.subscribe()
        assert fallen_cup_domain._bridge.subscribe.call_count == count

    def test_pose2d_parsed(self, fallen_cup_domain: FallenCupDomain):
        fallen_cup_domain._on_pose2d(_pose2d_msg())
        state = fallen_cup_domain.get_state()
        assert state["pose2d"] is not None
        assert state["pose2d"]["yaw"] == pytest.approx(3.10)
        assert state["pose2d"]["grip"]["x"] == pytest.approx(425.0)
        assert state["pose2d"]["confidence"] == pytest.approx(0.91)

    def test_pose2d_too_short_ignored(self, fallen_cup_domain: FallenCupDomain):
        fallen_cup_domain._on_pose2d({"data": [1.0, 2.0]})
        assert fallen_cup_domain.get_state()["pose2d"] is None

    def test_grasp_pose_parsed(self, fallen_cup_domain: FallenCupDomain):
        fallen_cup_domain._on_grasp_pose(_grasp_pose_msg())
        state = fallen_cup_domain.get_state()
        assert state["grasp_pose"] is not None
        assert state["grasp_pose"]["frame_id"] == "camera_color_optical_frame"
        assert state["grasp_pose"]["position"]["z"] == pytest.approx(0.41)

    def test_cups_parsed_and_joined_with_grasp(self, fallen_cup_domain: FallenCupDomain):
        fallen_cup_domain._on_cups_grasp_poses(_cups_grasp_poses_msg())
        fallen_cup_domain._on_cups_pose2d(_cups_pose2d_msg(2))
        state = fallen_cup_domain.get_state()
        assert state["count"] == 2
        assert state["cups"][0]["cup_id"] == 0
        assert state["cups"][0]["position"] is not None
        assert state["cups"][0]["position"]["z"] == pytest.approx(0.41)
        # NaN depth → position None
        assert state["cups"][1]["position"] is None

    def test_staleness_clears_data(self, fallen_cup_domain: FallenCupDomain, monkeypatch):
        fallen_cup_domain._on_pose2d(_pose2d_msg())
        fallen_cup_domain._on_cups_pose2d(_cups_pose2d_msg(1))
        assert fallen_cup_domain.get_state()["pose2d"] is not None

        real_monotonic = time.monotonic
        monkeypatch.setattr(
            "server.domains.fallen_cup.time.monotonic",
            lambda: real_monotonic() + 10.0,
        )
        state = fallen_cup_domain.get_state()
        assert state["pose2d"] is None
        assert state["count"] == 0
        assert state["cups"] == []

    def test_detection_running_states(self, fallen_cup_domain: FallenCupDomain):
        fallen_cup_domain._launcher._tasks = {}
        assert fallen_cup_domain.is_detection_running() is False

        task = _make_running_task(DETECT_COMMAND)
        fallen_cup_domain._launcher._tasks = {DETECT_COMMAND: task}
        assert fallen_cup_domain.is_detection_running() is True

        task.status = TaskStatus.IDLE
        assert fallen_cup_domain.is_detection_running() is False

    def test_build_detection_args_defaults(self, fallen_cup_domain: FallenCupDomain):
        args = fallen_cup_domain.build_detection_args()
        assert args["conf"] == "0.7"
        assert args["imgsz"] == "1280"
        assert args["use_depth"] == "true"
        assert args["device"] == "cpu"
        assert "weights_path" not in args  # 기본 config는 빈 문자열

    def test_build_detection_args_overrides(self, fallen_cup_domain: FallenCupDomain):
        args = fallen_cup_domain.build_detection_args(
            conf=0.5, imgsz=640, use_depth=False, weights_path="/abs/best.pt",
        )
        assert args["conf"] == "0.5"
        assert args["imgsz"] == "640"
        assert args["use_depth"] == "false"
        assert args["weights_path"] == "/abs/best.pt"

    def test_start_detection_calls_launcher(self, fallen_cup_domain: FallenCupDomain):
        task = _make_running_task(DETECT_COMMAND)
        fallen_cup_domain._launcher.start.return_value = task

        result = asyncio.run(fallen_cup_domain.start_detection({"conf": "0.7"}))
        fallen_cup_domain._launcher.start.assert_awaited_once_with(
            DETECT_COMMAND, {"conf": "0.7"},
        )
        assert result["name"] == DETECT_COMMAND
        assert result["status"] == "running"

    def test_stop_detection_calls_launcher(self, fallen_cup_domain: FallenCupDomain):
        result = asyncio.run(fallen_cup_domain.stop_detection())
        fallen_cup_domain._launcher.stop.assert_awaited_once_with(DETECT_COMMAND)
        assert result["status"] == "stopped"
