"""Tests for launch manager task classification."""

from server.ros.launch import (
    ALL_COMMANDS,
    BRINGUP_COMMANDS,
    SERVICE_COMMANDS,
    TASK_COMMANDS,
)


def test_cup_detection_in_service_commands():
    assert "cup_detection" in SERVICE_COMMANDS


def test_task_commands_empty():
    assert TASK_COMMANDS == set()


def test_all_commands_union():
    assert ALL_COMMANDS == BRINGUP_COMMANDS | TASK_COMMANDS | SERVICE_COMMANDS


def test_bringup_commands():
    assert BRINGUP_COMMANDS == {"bringup_sim", "bringup_real"}
