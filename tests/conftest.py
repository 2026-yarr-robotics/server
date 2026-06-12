"""Shared test fixtures."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server.config import AppSettings
from server.domains.fallen_cup import FallenCupDomain
from server.domains.robot import RobotDomain
from server.ros.bridge import RosBridge
from server.ros.launch import LaunchManager, RunningTask, TaskStatus


@pytest.fixture
def mock_bridge() -> MagicMock:
    bridge = MagicMock(spec=RosBridge)
    bridge.subscribe = MagicMock()
    bridge.call_service = AsyncMock()
    return bridge


@pytest.fixture
def mock_launcher() -> MagicMock:
    launcher = MagicMock(spec=LaunchManager)
    launcher.active_action_task = None
    launcher.bringup_task = None
    launcher.list_tasks = MagicMock(return_value=[])
    launcher.start = AsyncMock()
    launcher.stop = AsyncMock()
    launcher._tasks = {}
    return launcher


@pytest.fixture
def fallen_cup_domain(mock_bridge, mock_launcher) -> FallenCupDomain:
    return FallenCupDomain(mock_bridge, mock_launcher)


@pytest.fixture
def robot_domain(mock_bridge, mock_launcher) -> RobotDomain:
    settings = AppSettings()
    return RobotDomain(
        mock_bridge,
        mock_launcher,
        settings.robot.joint_states,
        settings.workspace_limits,
        settings.robot_home,
    )
