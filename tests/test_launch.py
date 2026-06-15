"""Tests for launch manager task classification."""

from server.ros.launch import (
    AGENT_COMMAND,
    AGENT_COMMANDS,
    ALL_COMMANDS,
    BRINGUP_COMMANDS,
    NON_ACTION_COMMANDS,
    SERVICE_COMMANDS,
    TASK_COMMANDS,
)


def test_fallen_cup_detect_in_service_commands():
    assert "fallen_cup_detect" in SERVICE_COMMANDS


def test_fallen_cup_recovery_in_task_commands():
    assert "fallen_cup_recovery" in TASK_COMMANDS


def test_outlier_cup_recovery_in_task_commands():
    assert "outlier_cup_recovery" in TASK_COMMANDS


def test_task_commands():
    assert TASK_COMMANDS == {"fallen_cup_recovery", "outlier_cup_recovery"}


def test_all_commands_union():
    assert ALL_COMMANDS == (
        BRINGUP_COMMANDS | TASK_COMMANDS | SERVICE_COMMANDS | AGENT_COMMANDS
    )


def test_bringup_commands():
    assert BRINGUP_COMMANDS == {"bringup_sim", "bringup_real"}


def test_agent_command():
    assert AGENT_COMMAND == "cup_stack_agent"
    assert AGENT_COMMANDS == {"cup_stack_agent"}


def test_agent_is_not_an_action_task():
    # The agent drives action tasks itself, so it must be excluded from the
    # single-action-task gate (alongside long-lived services).
    assert AGENT_COMMAND in NON_ACTION_COMMANDS
    assert NON_ACTION_COMMANDS == SERVICE_COMMANDS | AGENT_COMMANDS
