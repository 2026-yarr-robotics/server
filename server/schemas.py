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


# ── Calibration ───────────────────────────────────────────────────────────────

class CalibrationResponse(BaseModel):
    file: str = Field(..., description="저장 파일명")
    matrix: list[list[float]] = Field(..., description="4×4 동차 변환 행렬 (mm 단위)")
    shape: list[int] = Field(..., description="행렬 차원")


class CalibrationUpdateRequest(BaseModel):
    matrix: list[list[float]] = Field(..., description="4×4 동차 변환 행렬 (mm 단위)")
