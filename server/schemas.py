"""Pydantic request/response schemas for all API endpoints."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ── Robot ─────────────────────────────────────────────────────────────────────

class JointStateSchema(BaseModel):
    name: list[str] = []
    position: list[float] = Field(default=[], description="라디안")
    velocity: list[float] = Field(default=[], description="라디안/s")
    effort: list[float] = Field(default=[], description="N·m")


class ActiveTaskSchema(BaseModel):
    name: Optional[str] = None
    status: str = "idle"


class TaskSummarySchema(BaseModel):
    name: str
    command: str
    status: str
    pid: Optional[int] = Field(None, description="프로세스 종료 시 null")


class EEPositionSchema(BaseModel):
    x: float = Field(..., description="base_link X (m)")
    y: float = Field(..., description="base_link Y (m)")
    z: float = Field(..., description="base_link Z (m)")


class RobotStatusResponse(BaseModel):
    joints: JointStateSchema
    task: ActiveTaskSchema
    bringup: ActiveTaskSchema
    tasks: list[TaskSummarySchema]
    ee_position: Optional[EEPositionSchema] = None


class WorkspaceLimitsResponse(BaseModel):
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float
    grid_spacing: float


class BringupRequest(BaseModel):
    mode: str = Field("sim", description="`real`이면 `ip` 필드 필요")
    ip: str = Field("192.168.1.100", description="`mode=real`일 때 로봇 컨트롤러 IP")


class TaskStartRequest(BaseModel):
    task: str = Field(..., description="실행할 태스크 이름")
    args: dict[str, str] = Field(
        default={},
        description="launch 파일에 전달할 인자 (key:=value 형식으로 변환됨)",
    )


class TaskStopRequest(BaseModel):
    name: str = Field(..., description="중지할 태스크 이름")


class TaskStartedResponse(BaseModel):
    name: str
    status: str
    pid: Optional[int] = None


class TaskStoppedResponse(BaseModel):
    name: str
    status: str


class TaskLogResponse(BaseModel):
    name: str
    log: list[str]


class GripperRequest(BaseModel):
    command: str = Field(..., description="`open` 또는 `close`")


class GripperResponse(BaseModel):
    success: bool
    message: str


class MoveRequest(BaseModel):
    x: float = Field(..., description="base_link X (m)")
    y: float = Field(..., description="base_link Y (m)")
    z: float = Field(..., description="base_link Z (m)")
    mode: str = Field("absolute", description="`absolute` 또는 `relative`")


class MoveResponse(BaseModel):
    success: bool
    message: str
    position: Optional[EEPositionSchema] = None


# ── Cup Detection ─────────────────────────────────────────────────────────────

class PixelPoint(BaseModel):
    x: float = Field(..., description="픽셀 X")
    y: float = Field(..., description="픽셀 Y")


class BoundingBox(BaseModel):
    x_min: float
    y_min: float
    x_max: float
    y_max: float


class CupInfo(BaseModel):
    id: str = Field(..., description="프레임 내 고유 ID")
    label: str = Field(..., description="YOLO 클래스 레이블")
    confidence: float = Field(..., description="신뢰도 [0.0, 1.0]")
    position: Optional[EEPositionSchema] = Field(None, description="base_link 3D 좌표 (m); depth 실패 시 null")
    pixel: PixelPoint = Field(..., description="bbox 중심 픽셀 좌표")
    bbox: BoundingBox = Field(..., description="픽셀 단위 bbox")


class CupDetectionFrame(BaseModel):
    stamp: float = Field(..., description="UNIX 타임스탬프 (초)")
    frame_id: str = Field(..., description="좌표 기준 프레임")
    count: int = Field(..., description="감지된 컵 수")
    cups: list[CupInfo] = Field(default=[], description="감지된 컵 목록")


class CupTriggerRequest(BaseModel):
    cup_id: str = Field(..., description="감지 결과의 cups[].id 값")
    task: str = Field(..., description="cup_pyramid_web 또는 cup_unstack_web")


# ── Calibration ───────────────────────────────────────────────────────────────

class CalibrationResponse(BaseModel):
    file: str = Field(..., description="저장 파일명")
    matrix: list[list[float]] = Field(..., description="4×4 동차 변환 행렬 (mm 단위)")
    shape: list[int] = Field(..., description="행렬 차원")


class CalibrationUpdateRequest(BaseModel):
    matrix: list[list[float]] = Field(..., description="4×4 동차 변환 행렬 (mm 단위)")


# ── Pixel → World ──────────────────────────────────────────────────────────────

class PixelToWorldResponse(BaseModel):
    x: float = Field(..., description="base_link X (m)")
    y: float = Field(..., description="base_link Y (m)")
    z: float = Field(..., description="base_link Z (m)")
    depth_mm: int = Field(..., description="픽셀 깊이 (mm)")
    pixel_x: int
    pixel_y: int
