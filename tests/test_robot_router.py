"""Tests for robot REST endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.domains.cup_detection import CupDetectionDomain
from server.domains.robot import RobotDomain
from server.ros.launch import RunningTask, TaskStatus
from server.routers import robot as robot_router_module
from server.routers.robot import router


def _make_test_client(
    robot_domain: RobotDomain,
    cup_domain: CupDetectionDomain,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    robot_router_module.set_robot_domain(robot_domain)
    robot_router_module.set_cup_detection_domain(cup_domain)
    return TestClient(app)


def _make_running_task(name: str) -> RunningTask:
    process = MagicMock()
    process.pid = 1234
    process.returncode = None
    return RunningTask(name=name, command=name, args={}, process=process, status=TaskStatus.RUNNING)


@pytest.fixture
def client(robot_domain, cup_detection_domain) -> TestClient:
    return _make_test_client(robot_domain, cup_detection_domain)


class TestCupsEndpoint:
    def test_get_cups_empty(self, client: TestClient):
        resp = client.get("/api/robot/cups")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["cups"] == []
        assert data["frame_id"] == "base_link"

    def test_get_cups_with_data(self, client: TestClient, cup_detection_domain: CupDetectionDomain):
        cup_detection_domain._on_cup_poses({
            "header": {"stamp": {"sec": 1000, "nanosec": 0}, "frame_id": "base_link"},
            "poses": [{
                "cup_id": "cup_0",
                "label": "cup",
                "confidence": 0.9,
                "position": {"x": 0.3, "y": 0.0, "z": 0.3},
                "cx": 320.0,
                "cy": 240.0,
                "bbox": [300, 220, 340, 260],
                "pose_valid": True,
            }],
        })
        resp = client.get("/api/robot/cups")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["cups"][0]["id"] == "cup_0"


class TestRobotStatusEndpoint:
    def test_get_status(self, client: TestClient, robot_domain: RobotDomain):
        resp = client.get("/api/robot/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "joints" in data
        assert "task" in data
        assert "bringup" in data
        assert "tasks" in data


class TestPyramidConfigEndpoint:
    def test_get_pyramid_config_uses_configured_home_xy(
        self,
        client: TestClient,
    ):
        resp = client.get("/api/robot/config/pyramid")
        assert resp.status_code == 200
        data = resp.json()
        assert data["center"] == {"x": 0.45, "y": 0.0}
        assert data["slots"]["1l"]["x"] == 0.45
        assert data["slots"]["1l"]["y"] == -0.079
        assert data["slots"]["1m"]["x"] == 0.45
        assert data["slots"]["1m"]["y"] == 0.0


class TestTaskLogEndpoint:
    def test_missing_name(self, client: TestClient):
        resp = client.get("/api/robot/task/log")
        assert resp.status_code == 400

    def test_tail_out_of_range(self, client: TestClient):
        resp = client.get("/api/robot/task/log?name=gripper&tail=1000")
        assert resp.status_code == 400
