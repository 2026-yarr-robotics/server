"""Tests for Pydantic schemas."""

from server.schemas import (
    TaskStartedResponse,
    TaskStoppedResponse,
)


def test_task_started_response_pid_optional():
    r = TaskStartedResponse(name="gripper", status="running", pid=None)
    assert r.pid is None

    r2 = TaskStartedResponse(name="gripper", status="running", pid=1234)
    assert r2.pid == 1234


def test_task_stopped_response():
    r = TaskStoppedResponse(name="gripper", status="stopped")
    assert r.status == "stopped"
