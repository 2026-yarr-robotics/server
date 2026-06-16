"""Tests for robot REST endpoints."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.domains.fallen_cup import FallenCupDomain
from server.domains.robot import RobotDomain
from server.ros.launch import RunningTask, TaskStatus
from server.routers import robot as robot_router_module
from server.routers.robot import router


def _make_test_client(
    robot_domain: RobotDomain,
    fallen_domain: FallenCupDomain,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    robot_router_module.set_robot_domain(robot_domain)
    robot_router_module.set_fallen_cup_domain(fallen_domain)
    return TestClient(app)


def _make_running_task(name: str) -> RunningTask:
    process = MagicMock()
    process.pid = 1234
    process.returncode = None
    return RunningTask(name=name, command=name, args={}, process=process, status=TaskStatus.RUNNING)


@pytest.fixture
def client(robot_domain, fallen_cup_domain) -> TestClient:
    return _make_test_client(robot_domain, fallen_cup_domain)


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


class TestUnstackAllSkillEndpoint:
    """POST /api/robot/skill/unstack_all — script/unstack.sh 의 전체 해체 스킬."""

    def test_unstack_all_runs_full_top_down_sequence(
        self, client: TestClient, robot_domain: RobotDomain,
    ):
        # 단위 unstack 의 skill_api 호출만 mock — 슬롯 순서/nested 진행은 실제 로직.
        robot_domain._ensure_skill_api = AsyncMock()
        robot_domain._post_pyramid_step = AsyncMock(
            return_value={"success": True, "skill": "pyramid", "detail": "ok"}
        )

        resp = client.post("/api/robot/skill/unstack_all", json={"x": 0.40, "y": 0.10})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["completed"] == 6
        assert data["total"] == 6
        assert data["dest"] == {"x": 0.40, "y": 0.10}

        # 위 → 아래 해체 순서 + nested 1..6 진행.
        assert [s["slot"] for s in data["steps"]] == ["3m", "2r", "2l", "1r", "1m", "1l"]
        assert [s["nested"] for s in data["steps"]] == [1, 2, 3, 4, 5, 6]
        assert all(s["success"] for s in data["steps"])
        assert robot_domain._post_pyramid_step.await_count == 6

        # 각 단계의 pick 은 해당 슬롯, place 는 목적지 + 증가하는 place_z.
        slots = [c.args[0]["slot"] for c in robot_domain._post_pyramid_step.await_args_list]
        assert slots == ["3m", "2r", "2l", "1r", "1m", "1l"]
        place_zs = [c.args[0]["place_z"] for c in robot_domain._post_pyramid_step.await_args_list]
        assert place_zs == pytest.approx([0.313 + i * 0.0127 for i in range(6)])

    def test_unstack_all_defaults_to_unstack_sh_destination(
        self, client: TestClient, robot_domain: RobotDomain,
    ):
        robot_domain._ensure_skill_api = AsyncMock()
        robot_domain._post_pyramid_step = AsyncMock(
            return_value={"success": True, "skill": "pyramid", "detail": ""}
        )
        # 본문 비움 → DEST 기본 (0.40, 0.10) (unstack.sh DEST_X/DEST_Y 와 동일).
        resp = client.post("/api/robot/skill/unstack_all", json={})
        assert resp.status_code == 200
        assert resp.json()["dest"] == {"x": 0.40, "y": 0.10}

    def test_unstack_all_stops_and_reports_on_step_failure(
        self, client: TestClient, robot_domain: RobotDomain,
    ):
        robot_domain._ensure_skill_api = AsyncMock()
        # 첫 단계가 계속 실패 → max_retry 만큼 재시도 후 시퀀스 중단.
        robot_domain._post_pyramid_step = AsyncMock(
            side_effect=RuntimeError("502: boom")
        )
        resp = client.post(
            "/api/robot/skill/unstack_all",
            json={"x": 0.40, "y": 0.10, "max_retry": 2, "retry_delay": 0},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["completed"] == 0
        assert len(data["steps"]) == 1          # 첫 단계에서 멈춤
        assert data["steps"][0]["slot"] == "3m"
        assert data["steps"][0]["success"] is False
        assert data["steps"][0]["attempts"] == 2
        assert robot_domain._post_pyramid_step.await_count == 2  # max_retry 회 시도

    def test_unstack_all_invalid_dest_returns_422(self, client: TestClient):
        # 목적지 X 가 워크스페이스 밖 → 첫 컵을 옮기기 전에 422 로 빠른 실패.
        resp = client.post(
            "/api/robot/skill/unstack_all",
            json={"x": 0.99, "y": 0.10},
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
            "place_cup_tilt_deg": "8.0",
            "place_plus_y_cup_tilt_deg": "8.0",
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

    def test_state_503_when_domain_not_set(self, robot_domain):
        app = FastAPI()
        app.include_router(router)
        robot_router_module.set_robot_domain(robot_domain)
        robot_router_module.fallen_cup_domain = None
        c = TestClient(app)
        resp = c.get("/api/robot/fallen-cup/state")
        assert resp.status_code == 503


class TestOutlierCupEndpoints:
    def test_recovery_stops_skill_api_and_starts_task(
        self, client: TestClient, mock_launcher,
    ):
        mock_launcher.start.return_value = _make_running_task("outlier_cup_recovery")
        resp = client.post(
            "/api/robot/outlier-cup/recovery",
            json={"mode": "place", "dry_run": False, "sim": True},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "outlier_cup_recovery"

        # MoveItPy 컨트롤러 경합 방지: skill_api 먼저 정지
        mock_launcher.stop.assert_awaited_with("skill_api")

        called_command, called_args = mock_launcher.start.call_args[0]
        assert called_command == "outlier_cup_recovery"
        # multi_cup 은 오케스트레이터가 강제 ON → 인자로 보내지 않음
        assert called_args == {"mode": "place", "dry_run": "false", "sim": "true"}
        assert "multi_cup" not in called_args

    def test_recovery_defaults(self, client: TestClient, mock_launcher):
        mock_launcher.start.return_value = _make_running_task("outlier_cup_recovery")
        resp = client.post("/api/robot/outlier-cup/recovery", json={})
        assert resp.status_code == 200
        _, called_args = mock_launcher.start.call_args[0]
        assert called_args == {"mode": "drop", "dry_run": "false", "sim": "false"}

    def test_recovery_invalid_mode_returns_422(self, client: TestClient):
        resp = client.post("/api/robot/outlier-cup/recovery", json={"mode": "throw"})
        assert resp.status_code == 422

    def test_recovery_conflict_returns_409(self, client: TestClient, mock_launcher):
        mock_launcher.start.side_effect = RuntimeError("Task 'x' is already running")
        resp = client.post("/api/robot/outlier-cup/recovery", json={"mode": "drop"})
        assert resp.status_code == 409


def _routed_call_service(*, states, moves, control_ok=True, mode_ok=True):
    """Build an async side_effect for bridge.call_service that dispatches by
    service name. ``states`` is consumed once per get_robot_state call;
    ``moves`` once per move_line call."""
    state_it = iter(states)
    move_it = iter(moves)

    async def _call(service_name, service_type, args=None, timeout=10.0):
        if service_name.endswith("get_robot_state"):
            return {"robot_state": next(state_it), "success": True}
        if service_name.endswith("set_robot_control"):
            return {"success": control_ok}
        if service_name.endswith("set_robot_mode"):
            return {"success": mode_ok}
        if service_name.endswith("move_line"):
            return next(move_it)
        return {}

    return _call


class TestMoveSafetyStopRecovery:
    def test_move_success_no_recovery(
        self, client: TestClient, robot_domain: RobotDomain,
    ):
        robot_domain._bridge.call_service.side_effect = _routed_call_service(
            states=[], moves=[{"success": True}],
        )
        resp = client.post(
            "/api/robot/move", json={"x": 0.45, "y": 0.0, "z": 0.30},
        )
        assert resp.status_code == 200
        assert resp.json()["recovered"] is False

    def test_move_recovers_safe_stop_and_retries(
        self, client: TestClient, robot_domain: RobotDomain,
    ):
        # 1st move trips a safe stop; recover (SAFE_STOP -> STANDBY); retry ok.
        robot_domain._bridge.call_service.side_effect = _routed_call_service(
            states=[5, 1],  # SAFE_STOP detected, then STANDBY after reset
            moves=[{"success": False, "message": "safe stop"}, {"success": True}],
        )
        resp = client.post(
            "/api/robot/move", json={"x": 0.45, "y": 0.0, "z": 0.30},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["recovered"] is True

    def test_move_retry_uses_reduced_speed(
        self, client: TestClient, robot_domain: RobotDomain,
    ):
        calls: list[dict] = []
        states = iter([5, 1])
        results = iter([{"success": False, "message": "safe stop"}, {"success": True}])

        async def _call(service_name, service_type, args=None, timeout=10.0):
            if service_name.endswith("get_robot_state"):
                return {"robot_state": next(states), "success": True}
            if service_name.endswith("move_line"):
                calls.append(args)
                return next(results)
            return {"success": True}

        robot_domain._bridge.call_service.side_effect = _call
        resp = client.post(
            "/api/robot/move", json={"x": 0.45, "y": 0.0, "z": 0.30},
        )
        assert resp.status_code == 200
        assert len(calls) == 2
        # retry velocity/accel are scaled below the first attempt.
        assert calls[1]["vel"][0] < calls[0]["vel"][0]
        assert calls[1]["acc"][0] < calls[0]["acc"][0]

    def test_move_failure_without_safe_stop_not_retried(
        self, client: TestClient, robot_domain: RobotDomain,
    ):
        move_calls = {"n": 0}
        states = iter([1])  # STANDBY -> nothing to recover

        async def _call(service_name, service_type, args=None, timeout=10.0):
            if service_name.endswith("get_robot_state"):
                return {"robot_state": next(states), "success": True}
            if service_name.endswith("move_line"):
                move_calls["n"] += 1
                return {"success": False, "message": "planning failed"}
            return {"success": True}

        robot_domain._bridge.call_service.side_effect = _call
        resp = client.post(
            "/api/robot/move", json={"x": 0.45, "y": 0.0, "z": 0.30},
        )
        assert resp.status_code == 409  # surfaced, not retried
        assert move_calls["n"] == 1


class TestRecoverEndpoint:
    def test_recover_safe_stop_to_standby(
        self, client: TestClient, robot_domain: RobotDomain,
    ):
        robot_domain._bridge.call_service.side_effect = _routed_call_service(
            states=[5, 1], moves=[],
        )
        resp = client.post("/api/robot/recover")
        assert resp.status_code == 200
        body = resp.json()
        assert body["recovered"] is True
        assert body["from_state_name"] == "SAFE_STOP"
        assert body["to_state_name"] == "STANDBY"

    def test_recover_noop_when_standby(
        self, client: TestClient, robot_domain: RobotDomain,
    ):
        robot_domain._bridge.call_service.side_effect = _routed_call_service(
            states=[1], moves=[],
        )
        resp = client.post("/api/robot/recover")
        assert resp.status_code == 200
        body = resp.json()
        assert body["recovered"] is True
        assert "no safety stop" in body["detail"]

    def test_recover_emergency_stop_not_cleared(
        self, client: TestClient, robot_domain: RobotDomain,
    ):
        robot_domain._bridge.call_service.side_effect = _routed_call_service(
            states=[6], moves=[],  # EMERGENCY_STOP
        )
        resp = client.post("/api/robot/recover")
        assert resp.status_code == 200
        body = resp.json()
        assert body["recovered"] is False
        assert body["from_state_name"] == "EMERGENCY_STOP"


class TestSkillSafetyStopRecovery:
    def test_skill_failure_clears_safe_stop(self, robot_domain: RobotDomain):
        robot_domain._bridge.call_service.side_effect = _routed_call_service(
            states=[5, 1], moves=[],  # SAFE_STOP -> STANDBY
        )

        def boom() -> dict:
            raise RuntimeError("500: mid-skill safe stop")

        with pytest.raises(RuntimeError) as ei:
            asyncio.run(robot_domain._run_skill_call(boom))
        assert "safety stop cleared" in str(ei.value)

    def test_skill_failure_without_safe_stop_passthrough(
        self, robot_domain: RobotDomain,
    ):
        robot_domain._bridge.call_service.side_effect = _routed_call_service(
            states=[1], moves=[],  # STANDBY -> nothing to clear
        )

        def boom() -> dict:
            raise RuntimeError("422: bad request")

        with pytest.raises(RuntimeError) as ei:
            asyncio.run(robot_domain._run_skill_call(boom))
        assert "safety stop cleared" not in str(ei.value)
        assert "422" in str(ei.value)
