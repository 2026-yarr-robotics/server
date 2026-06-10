"""Tests for robot REST endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.domains.cup_detection import CupDetectionDomain
from server.domains.fallen_cup import FallenCupDomain
from server.domains.robot import RobotDomain
from server.ros.launch import RunningTask, TaskStatus
from server.routers import robot as robot_router_module
from server.routers.robot import router


def _make_test_client(
    robot_domain: RobotDomain,
    cup_domain: CupDetectionDomain,
    fallen_domain: FallenCupDomain,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    robot_router_module.set_robot_domain(robot_domain)
    robot_router_module.set_cup_detection_domain(cup_domain)
    robot_router_module.set_fallen_cup_domain(fallen_domain)
    return TestClient(app)


def _make_running_task(name: str) -> RunningTask:
    process = MagicMock()
    process.pid = 1234
    process.returncode = None
    return RunningTask(name=name, command=name, args={}, process=process, status=TaskStatus.RUNNING)


@pytest.fixture
def client(robot_domain, cup_detection_domain, fallen_cup_domain) -> TestClient:
    return _make_test_client(robot_domain, cup_detection_domain, fallen_cup_domain)


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
        assert data["slots"]["1l"]["y"] == -0.078
        assert data["slots"]["1m"]["x"] == 0.45
        assert data["slots"]["1m"]["y"] == 0.0


class TestPyramidSkillNested:
    """POST /api/robot/skill/pyramid 의 nested 파라미터 (기본 1, 하위호환)."""

    def test_pyramid_default_nested_keeps_base_pick_z(
        self, client: TestClient, robot_domain: RobotDomain,
    ):
        robot_domain._ensure_skill_api = AsyncMock()
        robot_domain._post_pyramid_step = AsyncMock(
            return_value={"success": True, "skill": "pyramid", "detail": ""}
        )
        # nested 미지정 → 기존과 동일하게 pick_z == pyramid_pick_z(0.313).
        resp = client.post(
            "/api/robot/skill/pyramid",
            json={"x": 0.40, "y": 0.10, "slot": "1l"},
        )
        assert resp.status_code == 200
        payload = robot_domain._post_pyramid_step.call_args[0][0]
        assert payload["pick_z"] == pytest.approx(0.313)

    def test_pyramid_nested_raises_pick_z(
        self, client: TestClient, robot_domain: RobotDomain,
    ):
        robot_domain._ensure_skill_api = AsyncMock()
        robot_domain._post_pyramid_step = AsyncMock(
            return_value={"success": True, "skill": "pyramid", "detail": ""}
        )
        resp = client.post(
            "/api/robot/skill/pyramid",
            json={"x": 0.40, "y": 0.10, "slot": "1l", "nested": 6},
        )
        assert resp.status_code == 200
        payload = robot_domain._post_pyramid_step.call_args[0][0]
        # nested=6 → pick_z = 0.313 + 5 * 0.0127
        assert payload["pick_z"] == pytest.approx(0.313 + 5 * 0.0127)

    def test_pyramid_nested_below_one_returns_422(self, client: TestClient):
        resp = client.post(
            "/api/robot/skill/pyramid",
            json={"x": 0.40, "y": 0.10, "slot": "1l", "nested": 0},
        )
        assert resp.status_code == 422


class TestUnstackSkillEndpoint:
    """POST /api/robot/skill/unstack — pyramid skill 의 역동작."""

    def test_unstack_picks_slot_and_nests_at_destination(
        self, client: TestClient, robot_domain: RobotDomain,
    ):
        # skill_api 호출은 mock: 픽업/배치 좌표가 올바른지만 검증.
        robot_domain._ensure_skill_api = AsyncMock()
        robot_domain._post_pyramid_step = AsyncMock(
            return_value={"success": True, "skill": "pyramid", "detail": ""}
        )

        resp = client.post(
            "/api/robot/skill/unstack",
            json={"slot": "3m", "x": 0.40, "y": 0.10, "nested": 1},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

        payload = robot_domain._post_pyramid_step.call_args[0][0]
        # pick = 캐시된 3m 슬롯 절대 좌표 (center 0.45,0.0 / degree 90 / 2층)
        assert payload["x"] == pytest.approx(0.45)
        assert payload["y"] == pytest.approx(0.0)
        assert payload["pick_z"] == pytest.approx(0.504)  # 0.318 + 2*0.093
        # place = 목적지 nest, nested=1 → place_z = pick_z(0.313)
        assert payload["place_x"] == pytest.approx(0.40)
        assert payload["place_y"] == pytest.approx(0.10)
        assert payload["place_z"] == pytest.approx(0.313)
        assert payload["slot"] == "3m"

    def test_unstack_place_z_grows_with_nested(
        self, client: TestClient, robot_domain: RobotDomain,
    ):
        robot_domain._ensure_skill_api = AsyncMock()
        robot_domain._post_pyramid_step = AsyncMock(
            return_value={"success": True, "skill": "pyramid", "detail": ""}
        )
        resp = client.post(
            "/api/robot/skill/unstack",
            json={"slot": "1l", "x": 0.40, "y": 0.10, "nested": 3},
        )
        assert resp.status_code == 200
        payload = robot_domain._post_pyramid_step.call_args[0][0]
        # nested=3 → place_z = 0.313 + 2 * 0.0127 (working nest_inc, 12.7mm)
        assert payload["place_z"] == pytest.approx(0.313 + 2 * 0.0127)

    def test_unstack_invalid_slot_returns_422(self, client: TestClient):
        resp = client.post(
            "/api/robot/skill/unstack",
            json={"slot": "9z", "x": 0.40, "y": 0.10, "nested": 1},
        )
        assert resp.status_code == 422

    def test_unstack_nested_below_one_returns_422(self, client: TestClient):
        resp = client.post(
            "/api/robot/skill/unstack",
            json={"slot": "3m", "x": 0.40, "y": 0.10, "nested": 0},
        )
        assert resp.status_code == 422


class TestTaskLogEndpoint:
    def test_missing_name(self, client: TestClient):
        resp = client.get("/api/robot/task/log")
        assert resp.status_code == 400

    def test_tail_out_of_range(self, client: TestClient):
        resp = client.get("/api/robot/task/log?name=gripper&tail=1000")
        assert resp.status_code == 400


class TestFallenCupEndpoints:
    def test_state_initial(self, client: TestClient):
        resp = client.get("/api/robot/fallen-cup/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["detection_running"] is False
        assert data["count"] == 0
        assert data["cups"] == []
        assert data["pose2d"] is None
        assert data["grasp_pose"] is None

    def test_detection_start_calls_launcher(
        self, client: TestClient, fallen_cup_domain: FallenCupDomain, mock_launcher,
    ):
        mock_launcher.start.return_value = _make_running_task("fallen_cup_detect")
        resp = client.post(
            "/api/robot/fallen-cup/detection/start",
            json={"conf": 0.5, "imgsz": 640, "use_depth": False},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "fallen_cup_detect"

        called_command, called_args = mock_launcher.start.call_args[0]
        assert called_command == "fallen_cup_detect"
        assert called_args["conf"] == "0.5"
        assert called_args["imgsz"] == "640"
        assert called_args["use_depth"] == "false"

    def test_detection_start_conflict_returns_409(
        self, client: TestClient, mock_launcher,
    ):
        mock_launcher.start.side_effect = RuntimeError("already running")
        resp = client.post("/api/robot/fallen-cup/detection/start", json={})
        assert resp.status_code == 409

    def test_detection_stop(self, client: TestClient, mock_launcher):
        resp = client.post("/api/robot/fallen-cup/detection/stop")
        assert resp.status_code == 200
        mock_launcher.stop.assert_awaited_with("fallen_cup_detect")

    def test_recovery_stops_skill_api_and_starts_task(
        self, client: TestClient, mock_launcher,
    ):
        mock_launcher.start.return_value = _make_running_task("fallen_cup_recovery")
        resp = client.post(
            "/api/robot/fallen-cup/recovery",
            json={"mode": "place", "multi_cup": True, "dry_run": False, "sim": True},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "fallen_cup_recovery"

        # MoveItPy 컨트롤러 경합 방지: skill_api 먼저 정지
        mock_launcher.stop.assert_awaited_with("skill_api")

        called_command, called_args = mock_launcher.start.call_args[0]
        assert called_command == "fallen_cup_recovery"
        assert called_args == {
            "mode": "place", "multi_cup": "true", "dry_run": "false", "sim": "true",
        }

    def test_recovery_invalid_mode_returns_422(self, client: TestClient):
        resp = client.post("/api/robot/fallen-cup/recovery", json={"mode": "throw"})
        assert resp.status_code == 422

    def test_recovery_z_safety_params_forwarded(self, client: TestClient, mock_launcher):
        """그리퍼-바닥 충돌 방지용 Z 안전 파라미터가 launch 인자로 전달되는지."""
        mock_launcher.start.return_value = _make_running_task("fallen_cup_recovery")
        resp = client.post(
            "/api/robot/fallen-cup/recovery",
            json={"mode": "place", "stand_cup_margin_m": 0.10, "place_safe_z_min": 0.20},
        )
        assert resp.status_code == 200
        _, called_args = mock_launcher.start.call_args[0]
        assert called_args["stand_cup_margin_m"] == "0.1"
        assert called_args["place_safe_z_min"] == "0.2"

    def test_recovery_z_safety_params_omitted_uses_launch_defaults(
        self, client: TestClient, mock_launcher,
    ):
        mock_launcher.start.return_value = _make_running_task("fallen_cup_recovery")
        resp = client.post("/api/robot/fallen-cup/recovery", json={"mode": "place"})
        assert resp.status_code == 200
        _, called_args = mock_launcher.start.call_args[0]
        # 생략 시 launch 기본값 사용 → args에 포함하지 않음
        assert "stand_cup_margin_m" not in called_args
        assert "place_safe_z_min" not in called_args

    def test_recovery_conflict_returns_409(self, client: TestClient, mock_launcher):
        mock_launcher.start.side_effect = RuntimeError("Task 'x' is already running")
        resp = client.post("/api/robot/fallen-cup/recovery", json={"mode": "drop"})
        assert resp.status_code == 409

    def test_state_503_when_domain_not_set(self, robot_domain, cup_detection_domain):
        app = FastAPI()
        app.include_router(router)
        robot_router_module.set_robot_domain(robot_domain)
        robot_router_module.set_cup_detection_domain(cup_detection_domain)
        robot_router_module.fallen_cup_domain = None
        c = TestClient(app)
        resp = c.get("/api/robot/fallen-cup/state")
        assert resp.status_code == 503
