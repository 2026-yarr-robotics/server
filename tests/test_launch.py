"""Tests for launch manager task classification."""

from server.ros.launch import (
    ALL_COMMANDS,
    BRINGUP_COMMANDS,
    SERVICE_COMMANDS,
    TASK_COMMANDS,
)


def test_fallen_cup_detect_in_service_commands():
    assert "fallen_cup_detect" in SERVICE_COMMANDS


def test_fallen_cup_recovery_in_task_commands():
    assert "fallen_cup_recovery" in TASK_COMMANDS


def test_task_commands():
    assert TASK_COMMANDS == {"fallen_cup_recovery"}


def test_all_commands_union():
    assert ALL_COMMANDS == BRINGUP_COMMANDS | TASK_COMMANDS | SERVICE_COMMANDS


def test_bringup_commands():
    assert BRINGUP_COMMANDS == {"bringup_sim", "bringup_real"}
