"""Pydantic request/response schemas for all API endpoints.

Every model carries a ``model_config["json_schema_extra"]["example"]`` so
the OpenAPI schema (and the Swagger UI "Example Value") shows a realistic
sample payload for each request and response.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


def _example(value: dict) -> dict:
    return {"json_schema_extra": {"example": value}}


# ── Robot ─────────────────────────────────────────────────────────────────────

class JointStateSchema(BaseModel):
    name: list[str] = []
    position: list[float] = Field(default=[], description="라디안")
    velocity: list[float] = Field(default=[], description="라디안/s")
    effort: list[float] = Field(default=[], description="N·m")

    model_config = _example({
        "name": ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6"],
        "position": [0.0, -0.262, 1.571, 0.0, 1.047, 0.0],
        "velocity": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "effort": [0.12, 1.84, 0.95, 0.05, 0.21, 0.0],
    })


class ActiveTaskSchema(BaseModel):
    name: Optional[str] = None
    status: str = "idle"

    model_config = _example({"name": None, "status": "idle"})


class TaskSummarySchema(BaseModel):
    name: str
    command: str
    status: str
    pid: Optional[int] = Field(None, description="프로세스 종료 시 null")

    model_config = _example({
        "name": "gripper",
        "command": "ros2 launch cup_stack gripper.launch.py",
        "status": "running",
        "pid": 12345,
    })


class EEPositionSchema(BaseModel):
    x: float = Field(..., description="base_link X (m)")
    y: float = Field(..., description="base_link Y (m)")
    z: float = Field(..., description="base_link Z (m)")

    model_config = _example({"x": 0.45, "y": -0.12, "z": 0.30})


class GripperStateSchema(BaseModel):
    width_mm: Optional[float] = Field(
        None, description="Live finger width in mm; null when stale/unknown"
    )

    model_config = _example({"width_mm": 74.8})


class RobotStatusResponse(BaseModel):
    joints: JointStateSchema
    task: ActiveTaskSchema
    bringup: ActiveTaskSchema
    tasks: list[TaskSummarySchema]
    ee_position: Optional[EEPositionSchema] = None
    gripper: Optional[GripperStateSchema] = None

    model_config = _example({
        "joints": {
            "name": ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6"],
            "position": [0.0, -0.262, 1.571, 0.0, 1.047, 0.0],
            "velocity": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "effort": [0.12, 1.84, 0.95, 0.05, 0.21, 0.0],
        },
        "task": {"name": None, "status": "idle"},
        "bringup": {"name": "bringup_sim", "status": "running"},
        "tasks": [{
            "name": "gripper",
            "command": "ros2 launch cup_stack gripper.launch.py",
            "status": "running",
            "pid": 12345,
        }],
        "ee_position": {"x": 0.45, "y": -0.12, "z": 0.30},
        "gripper": {"width_mm": 74.8},
    })


class WorkspaceLimitsResponse(BaseModel):
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float
    grid_spacing: float

    model_config = _example({
        "x_min": 0.20, "x_max": 0.65,
        "y_min": -0.35, "y_max": 0.35,
        "z_min": 0.05, "z_max": 0.50,
        "grid_spacing": 0.05,
    })


class BringupRequest(BaseModel):
    mode: str = Field("sim", description="`real`이면 `ip` 필드 필요")
    ip: str = Field("192.168.1.100", description="`mode=real`일 때 로봇 컨트롤러 IP")

    model_config = _example({"mode": "sim", "ip": "192.168.1.100"})


class TaskStartRequest(BaseModel):
    task: str = Field(..., description="실행할 태스크 이름")
    args: dict[str, str] = Field(
        default={},
        description="launch 파일에 전달할 인자 (key:=value 형식으로 변환됨)",
    )

    model_config = _example({"task": "gripper", "args": {}})


class TaskStopRequest(BaseModel):
    name: str = Field(..., description="중지할 태스크 이름")

    model_config = _example({"name": "gripper"})


class TaskStartedResponse(BaseModel):
    name: str
    status: str
    pid: Optional[int] = None

    model_config = _example({
        "name": "gripper", "status": "running", "pid": 12345,
    })


class TaskStoppedResponse(BaseModel):
    name: str
    status: str

    model_config = _example({"name": "gripper", "status": "stopped"})


class TaskLogResponse(BaseModel):
    name: str
    log: list[str]

    model_config = _example({
        "name": "gripper",
        "log": [
            "[INFO] [launch]: process started",
            "[INFO] [gripper]: opening",
            "[INFO] [gripper]: opened",
        ],
    })


class GripperRequest(BaseModel):
    command: str = Field(..., description="`open` 또는 `close`")

    model_config = _example({"command": "close"})


class GripperResponse(BaseModel):
    success: bool
    message: str

    model_config = _example({"success": True, "message": "gripper closed"})


class MoveRequest(BaseModel):
    x: float = Field(..., description="base_link X (m)")
    y: float = Field(..., description="base_link Y (m)")
    z: float = Field(..., description="base_link Z (m)")
    mode: str = Field("absolute", description="`absolute` 또는 `relative`")

    model_config = _example({
        "x": 0.45, "y": -0.12, "z": 0.30, "mode": "absolute",
    })


class MoveResponse(BaseModel):
    success: bool
    message: str
    position: Optional[EEPositionSchema] = None

    model_config = _example({
        "success": True,
        "message": "move completed",
        "position": {"x": 0.45, "y": -0.12, "z": 0.30},
    })


# ── Cup Detection ─────────────────────────────────────────────────────────────

class PixelPoint(BaseModel):
    x: float = Field(..., description="픽셀 X")
    y: float = Field(..., description="픽셀 Y")

    model_config = _example({"x": 320.5, "y": 240.0})


class BoundingBox(BaseModel):
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    model_config = _example({
        "x_min": 300.0, "y_min": 220.0, "x_max": 341.0, "y_max": 270.0,
    })


class CupInfo(BaseModel):
    id: str = Field(..., description="프레임 내 고유 ID")
    label: str = Field(..., description="YOLO 클래스 레이블")
    confidence: float = Field(..., description="신뢰도 [0.0, 1.0]")
    position: Optional[EEPositionSchema] = Field(None, description="base_link 3D 좌표 (m); depth 실패 시 null")
    pixel: PixelPoint = Field(..., description="bbox 중심 픽셀 좌표")
    bbox: BoundingBox = Field(..., description="픽셀 단위 bbox")

    model_config = _example({
        "id": "cup_0",
        "label": "cup",
        "confidence": 0.94,
        "position": {"x": 0.45, "y": -0.12, "z": 0.05},
        "pixel": {"x": 320.5, "y": 240.0},
        "bbox": {"x_min": 300.0, "y_min": 220.0, "x_max": 341.0, "y_max": 270.0},
    })


class CupDetectionFrame(BaseModel):
    stamp: float = Field(..., description="UNIX 타임스탬프 (초)")
    frame_id: str = Field(..., description="좌표 기준 프레임")
    count: int = Field(..., description="감지된 컵 수")
    cups: list[CupInfo] = Field(default=[], description="감지된 컵 목록")

    model_config = _example({
        "stamp": 1747291383.512,
        "frame_id": "base_link",
        "count": 1,
        "cups": [{
            "id": "cup_0",
            "label": "cup",
            "confidence": 0.94,
            "position": {"x": 0.45, "y": -0.12, "z": 0.05},
            "pixel": {"x": 320.5, "y": 240.0},
            "bbox": {"x_min": 300.0, "y_min": 220.0, "x_max": 341.0, "y_max": 270.0},
        }],
    })


# ── Calibration ───────────────────────────────────────────────────────────────

class CalibrationResponse(BaseModel):
    file: str = Field(..., description="저장 파일명")
    matrix: list[list[float]] = Field(..., description="4×4 동차 변환 행렬 (mm 단위)")
    shape: list[int] = Field(..., description="행렬 차원")

    model_config = _example({
        "file": "handeye_handineye.npy",
        "matrix": [
            [1.0, 0.0, 0.0, 30.5],
            [0.0, 1.0, 0.0, -12.0],
            [0.0, 0.0, 1.0, 415.2],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "shape": [4, 4],
    })


class CalibrationUpdateRequest(BaseModel):
    matrix: list[list[float]] = Field(..., description="4×4 동차 변환 행렬 (mm 단위)")

    model_config = _example({
        "matrix": [
            [1.0, 0.0, 0.0, 30.5],
            [0.0, 1.0, 0.0, -12.0],
            [0.0, 0.0, 1.0, 415.2],
            [0.0, 0.0, 0.0, 1.0],
        ],
    })


# ── Pick Skill ────────────────────────────────────────────────────────────────

class PickSkillRequest(BaseModel):
    """Body for POST /api/robot/skill/pick.

    좌표는 **컵 윗면 중앙** 기준(base_link, m). ``cup_top_z``를 주면
    skill_api_node가 ``gripper_z = cup_top_z + cup_grip_z_offset``로
    변환한다. ``z``(그리퍼 raw Z)를 직접 줄 수도 있고, ``nested_count``를
    주면 skill_api_node가 ``pick_z_base + (nested_count - 1) * nest_inc``로
    그리퍼 Z를 산출한다. 셋 중 하나는 필수.
    """

    x: float = Field(..., description="컵 윗면 중앙 X (base_link, m)")
    y: float = Field(..., description="컵 윗면 중앙 Y (base_link, m)")
    cup_top_z: Optional[float] = Field(
        None, description="컵 윗면 중앙 Z (m). 서버에서 grip offset 가산"
    )
    z: Optional[float] = Field(
        None, description="그리퍼 raw Z (m). cup_top_z 대신 직접 지정 시"
    )
    nested_count: Optional[int] = Field(
        None, ge=1,
        description="소스 스택에 nested 컵 개수. ROS 2 skill_api_node가 Z를 산출",
    )
    ori: Optional[dict] = Field(
        None, description="그리퍼 방향 quaternion {x,y,z,w}; 미지정 시 down"
    )

    model_config = _example({"x": 0.45, "y": -0.12, "nested_count": 1})


class PickSkillResponse(BaseModel):
    success: bool
    skill: str
    detail: str = ""

    model_config = _example({
        "success": True, "skill": "pick", "detail": "gripper_z=0.1500",
    })


# ── Pyramid Skill ─────────────────────────────────────────────────────────────

PyramidSlotKey = Literal["1l", "1m", "1r", "2l", "2r", "3m"]


class PyramidConfigCenter(BaseModel):
    x: float = Field(..., description="피라미드 중심 X (base_link, m)")
    y: float = Field(..., description="피라미드 중심 Y (base_link, m)")

    model_config = _example({"x": 0.50, "y": 0.00})


class PyramidSlotXYZ(BaseModel):
    x: float
    y: float
    z: float

    model_config = _example({"x": 0.50, "y": 0.00, "z": 0.323})


class PyramidConfigResponse(BaseModel):
    """3-2-1 피라미드 설정 + 6개 슬롯의 절대 place 좌표 캐시."""

    center: PyramidConfigCenter
    degree: float = Field(..., description="행 방향 yaw (deg, [0, 360)). +x 기준 반시계")
    pick_z: float = Field(..., description="그리퍼 pick Z (base_link, m)")
    slots: dict[str, PyramidSlotXYZ] = Field(
        ..., description="키: 1l/1m/1r/2l/2r/3m, 값: place 절대 좌표"
    )

    model_config = _example({
        "center": {"x": 0.50, "y": 0.00},
        "degree": 90.0,
        "pick_z": 0.313,
        "slots": {
            "1l": {"x": 0.50, "y": -0.079, "z": 0.323},
            "1m": {"x": 0.50, "y":  0.000, "z": 0.323},
            "1r": {"x": 0.50, "y":  0.079, "z": 0.323},
            "2l": {"x": 0.50, "y": -0.0395, "z": 0.418},
            "2r": {"x": 0.50, "y":  0.0395, "z": 0.418},
            "3m": {"x": 0.50, "y":  0.000, "z": 0.513},
        },
    })


class PyramidConfigUpdate(BaseModel):
    """POST /api/robot/config/pyramid 본문. 지정한 필드만 갱신."""

    center: Optional[PyramidConfigCenter] = Field(
        None, description="피라미드 중심 XY (생략 시 기존값 유지)"
    )
    degree: Optional[float] = Field(
        None, description="행 방향 yaw (deg, 입력값은 % 360 wrap)"
    )
    pick_z: Optional[float] = Field(
        None, description="그리퍼 pick Z (base_link, m)"
    )

    model_config = _example({"degree": 45.0})


class PyramidSkillRequest(BaseModel):
    """POST /api/robot/skill/pyramid 본문. pick XY + 놓을 slot 키만 받음.

    피라미드 중심·yaw·pick_z 는 /api/robot/config/pyramid 에 저장된 값을
    서버가 자동 주입하므로 본문에 포함하지 않는다.
    """

    x: float = Field(..., description="pick 컵 윗면 중앙 X (base_link, m)")
    y: float = Field(..., description="pick 컵 윗면 중앙 Y (base_link, m)")
    slot: PyramidSlotKey = Field(
        ..., description="놓을 슬롯 키: 1l/1m/1r (bottom), 2l/2r (mid), 3m (top)"
    )

    model_config = _example({"x": 0.40, "y": 0.10, "slot": "1l"})


class PyramidSkillResponse(BaseModel):
    success: bool
    skill: str = "pyramid"
    detail: str = ""

    model_config = _example({
        "success": True,
        "skill": "pyramid",
        "detail": "slot=1l pick=(0.40,0.10,0.313) place=(0.50,-0.079,0.323)",
    })


# ── Pixel → World ──────────────────────────────────────────────────────────────

class PixelToWorldResponse(BaseModel):
    x: float = Field(..., description="base_link X (m)")
    y: float = Field(..., description="base_link Y (m)")
    z: float = Field(..., description="base_link Z (m)")
    depth_mm: int = Field(..., description="픽셀 깊이 (mm)")
    pixel_x: int
    pixel_y: int

    model_config = _example({
        "x": 0.45, "y": -0.12, "z": 0.05,
        "depth_mm": 512,
        "pixel_x": 320, "pixel_y": 240,
    })
