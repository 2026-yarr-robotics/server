"""Tests for Pydantic schemas."""

from server.schemas import (
    BoundingBox,
    CupDetectionFrame,
    CupInfo,
    EEPositionSchema,
    PixelPoint,
    TaskStartedResponse,
    TaskStoppedResponse,
)


def test_pixel_point():
    p = PixelPoint(x=320.0, y=240.0)
    assert p.x == 320.0
    assert p.y == 240.0


def test_bounding_box():
    bb = BoundingBox(x_min=300, y_min=220, x_max=340, y_max=260)
    assert bb.x_min == 300
    assert bb.x_max == 340


def test_cup_info_with_position():
    cup = CupInfo(
        id="cup_0",
        label="cup",
        confidence=0.95,
        position=EEPositionSchema(x=0.35, y=0.02, z=0.30),
        pixel=PixelPoint(x=320, y=240),
        bbox=BoundingBox(x_min=300, y_min=220, x_max=340, y_max=260),
    )
    assert cup.id == "cup_0"
    assert cup.position is not None
    assert cup.position.x == 0.35


def test_cup_info_without_position():
    cup = CupInfo(
        id="cup_1",
        label="cup",
        confidence=0.7,
        position=None,
        pixel=PixelPoint(x=100, y=200),
        bbox=BoundingBox(x_min=80, y_min=180, x_max=120, y_max=220),
    )
    assert cup.position is None


def test_cup_detection_frame_empty():
    frame = CupDetectionFrame(stamp=1715165696.789, frame_id="base_link", count=0, cups=[])
    assert frame.count == 0
    assert frame.cups == []


def test_cup_detection_frame_with_cups():
    frame = CupDetectionFrame(
        stamp=1715165696.789,
        frame_id="base_link",
        count=1,
        cups=[
            CupInfo(
                id="cup_0",
                label="cup",
                confidence=0.95,
                position=EEPositionSchema(x=0.35, y=0.02, z=0.30),
                pixel=PixelPoint(x=320, y=240),
                bbox=BoundingBox(x_min=300, y_min=220, x_max=340, y_max=260),
            )
        ],
    )
    assert frame.count == 1
    assert len(frame.cups) == 1
    assert frame.cups[0].id == "cup_0"


def test_task_started_response_pid_optional():
    r = TaskStartedResponse(name="gripper", status="running", pid=None)
    assert r.pid is None

    r2 = TaskStartedResponse(name="gripper", status="running", pid=1234)
    assert r2.pid == 1234


def test_task_stopped_response():
    r = TaskStoppedResponse(name="gripper", status="stopped")
    assert r.status == "stopped"
