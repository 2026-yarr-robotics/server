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
    ros_stop_success: bool = False

    model_config = _example({"name": "gripper", "status": "stopped"})


class StopAllRequest(BaseModel):
    home: bool = Field(
        True, description="정지 후 팔을 HOME 으로 복귀시킬지 여부"
    )

    model_config = _example({"home": True})


class StopAllResponse(BaseModel):
    """실행 중인 skill/task 즉시 정지 + HOME 복귀 결과."""

    success: bool
    ros_stop: bool = False            # DRCF MoveStop 퀵스탑 전송 성공
    interrupted: bool = False         # 진행 중인 모션을 실제로 끊었는지
    killed_tasks: list[str] = []      # kill 한 task 이름들 (action task/agent)
    homed: bool = False               # HOME 복귀 완료 여부
    detail: str = ""

    model_config = _example({
        "success": True,
        "ros_stop": True,
        "interrupted": True,
        "killed_tasks": [],
        "homed": True,
        "detail": "interrupted + homed",
    })


class HomeResponse(BaseModel):
    """팔을 조인트 HOME 으로 복귀시킨 결과 (인터럽트 없는 단순 HOME)."""

    success: bool
    homed: bool = False               # HOME 복귀 완료 여부
    detail: str = ""

    model_config = _example({
        "success": True,
        "homed": True,
        "detail": "homed",
    })


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
    recovered: bool = False  # True if a safety stop was auto-cleared mid-move

    model_config = _example({
        "success": True,
        "message": "move completed",
        "position": {"x": 0.45, "y": -0.12, "z": 0.30},
        "recovered": False,
    })


class RecoverResponse(BaseModel):
    """Result of clearing a Doosan safety stop (accel/vel-limit 'yellow light')."""
    recovered: bool
    from_state: Optional[int] = None
    from_state_name: str
    to_state: Optional[int] = None
    to_state_name: str
    detail: str

    model_config = _example({
        "recovered": True,
        "from_state": 5,
        "from_state_name": "SAFE_STOP",
        "to_state": 1,
        "to_state_name": "STANDBY",
        "detail": "reset from SAFE_STOP to STANDBY",
    })


class UserCommandRequest(BaseModel):
    text: str = Field(
        ...,
        description="자연어 명령. cup_stack_agent 의 start.sh --real-api 를 띄우며 "
        "USER_COMMAND 로 전달된다 (aggregator 의 user_command 파라미터로 흘러간다).",
    )

    model_config = _example({"text": "3단 피라미드 쌓아줘"})


class UserCommandResponse(BaseModel):
    success: bool
    message: str

    model_config = _example({
        "success": True,
        "message": "agent started: 3단 피라미드 쌓아줘",
    })


# ── Fallen Cup ────────────────────────────────────────────────────────────────

class FallenCupDetectionStartRequest(BaseModel):
    """Body for POST /api/robot/fallen-cup/detection/start.

    생략한 필드는 서버 설정(FallenCupConfig) 기본값을 사용한다.
    """

    conf: Optional[float] = Field(None, ge=0.0, le=1.0, description="YOLO confidence threshold")
    imgsz: Optional[int] = Field(None, ge=64, description="YOLO 입력 크기")
    use_depth: Optional[bool] = Field(None, description="depth로 3D grasp_pose 생성 여부")
    weights_path: Optional[str] = Field(
        None, description="YOLO weights(.pt) 절대경로. 생략 시 서버 설정/launch 기본값"
    )

    model_config = _example({"conf": 0.70, "imgsz": 1280, "use_depth": True})


class MouthUpCupDetectionStartRequest(BaseModel):
    """Body for POST /api/robot/mouth-up-cup/detection/start.

    생략한 필드는 서버 설정(MouthUpCupConfig) 기본값을 사용한다.
    """

    conf: Optional[float] = Field(None, ge=0.0, le=1.0, description="YOLO confidence threshold")
    imgsz: Optional[int] = Field(None, ge=64, description="YOLO 입력 크기")
    target_class_name: Optional[str] = Field(
        None, description="검출할 YOLO 클래스 (기본 'mouth-up-cup')"
    )
    weights_path: Optional[str] = Field(
        None, description="YOLO weights(.pt) 절대경로. 생략 시 서버 설정/launch 기본값"
    )

    model_config = _example({"conf": 0.25, "imgsz": 1280})


class FallenCupRecoveryRequest(BaseModel):
    """Body for POST /api/robot/fallen-cup/recovery.

    stand_fallen_cup 태스크(1회 실행)를 시작한다. 진행 상황은
    ``/ws/task/log`` · ``/ws/robot/state`` 로 모니터링하고, 중지는
    ``POST /api/robot/task/stop`` ``{"name": "fallen_cup_recovery"}`` 사용.
    """

    mode: Literal["drop", "place"] = Field(
        "drop", description="lift 후 동작: drop(그 자리에 놓기) / place(옮겨 세우기)"
    )
    multi_cup: bool = Field(False, description="여러 fallen cup 순차 처리")
    dry_run: bool = Field(False, description="approach까지만 (gripper/descend/lift 스킵)")
    sim: bool = Field(False, description="카메라/그리퍼 HW 우회 (MoveIt virtual)")
    stand_cup_margin_m: Optional[float] = Field(
        -0.065, ge=-0.10, le=0.30,
        description="place 모드: 컵 바닥-테이블 여유 (m). 기본 -0.065로 launch 기본(-0.05)보다 1.5cm 낮게 release. "
                    "값을 낮추면 release 높이가 내려가고, 키우면 올라감",
    )
    place_safe_z_min: Optional[float] = Field(
        None, ge=0.05, le=0.40,
        description="place 모드: flange 최저 안전 z (m). 생략 시 launch 기본값(0.15). "
                    "그리퍼-바닥 충돌 방지 클램프",
    )
    place_cup_tilt_deg: Optional[float] = Field(
        8.0, ge=-30.0, le=30.0,
        description="place 모드: cup을 vertical에서 -EE_Z 방향으로 기울이는 각도(deg). "
                    "기본 8deg를 서버 API에서 launch arg로 전달 (15deg에서 일부 컵이 넘어져 낮춤)",
    )
    place_plus_y_cup_tilt_deg: Optional[float] = Field(
        8.0, ge=-30.0, le=30.0,
        description="place 모드: plus_y auto_swing 발동 시 사용할 cup tilt 각도(deg). "
                    "기본 8deg를 서버 API에서 launch arg로 전달 (15deg에서 일부 컵이 넘어져 낮춤)",
    )

    model_config = _example({"mode": "place", "multi_cup": False, "dry_run": False, "sim": False})


# ── Outlier Cup ───────────────────────────────────────────────────────────────

class OutlierCupRecoveryRequest(BaseModel):
    """Body for POST /api/robot/outlier-cup/recovery.

    outlier_cup_recovery 오케스트레이터 태스크(1회 실행)를 시작한다 — 한 프레임의
    fallen cup 을 base_link 최근접 순으로 전부 세운 뒤, mouth-up cup 을 전부 뒤집어
    내려놓고 HOME 복귀 후 종료한다. fallen-only 인 ``fallen_cup_recovery`` 는
    그대로 유지되며, 이 엔드포인트는 그 상위 집합(orchestrator)이다.

    진행 상황은 ``/ws/task/log`` · ``/ws/robot/state`` 로 모니터링하고, 중지는
    ``POST /api/robot/task/stop`` ``{"name": "outlier_cup_recovery"}`` 사용.
    ``multi_cup`` 은 오케스트레이터가 강제 ON 이라 노출하지 않는다.
    """

    mode: Literal["drop", "place"] = Field(
        "drop", description="fallen lift 후 동작: drop(그 자리에 놓기) / place(옮겨 세우기). "
                            "mouth-up 단계와는 무관",
    )
    dry_run: bool = Field(False, description="approach까지만 (gripper/insert/lift 스킵, 양 스킬 공통)")
    sim: bool = Field(False, description="카메라/그리퍼 HW 우회 (MoveIt virtual)")

    model_config = _example({"mode": "drop", "dry_run": False, "sim": False})


class FallenCupPixel(BaseModel):
    x: float
    y: float

    model_config = _example({"x": 412.0, "y": 305.5})


class FallenCupPose2D(BaseModel):
    """단일 컵 2D 인식 결과 (/fallen_cup/pose2d)."""

    top: FallenCupPixel
    bottom: FallenCupPixel
    direction: FallenCupPixel
    yaw: float = Field(..., description="컵 방향 yaw (rad, 카메라 프레임)")
    grip: FallenCupPixel
    confidence: float
    top_width: float
    bottom_width: float

    model_config = _example({
        "top": {"x": 412.0, "y": 305.5},
        "bottom": {"x": 520.0, "y": 310.0},
        "direction": {"x": -0.99, "y": -0.04},
        "yaw": 3.10,
        "grip": {"x": 425.0, "y": 306.0},
        "confidence": 0.91,
        "top_width": 48.0,
        "bottom_width": 72.0,
    })


class FallenCupGraspPose(BaseModel):
    """3D grasp 좌표 (/fallen_cup/grasp_pose, 카메라 optical frame)."""

    frame_id: str
    position: EEPositionSchema
    orientation: dict = Field(..., description="quaternion {x, y, z, w}")

    model_config = _example({
        "frame_id": "camera_color_optical_frame",
        "position": {"x": 0.012, "y": -0.034, "z": 0.41},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    })


class FallenCupItem(BaseModel):
    """multi-cup 인식 결과의 개별 컵."""

    cup_id: int
    yaw: float
    grip_pixel: FallenCupPixel
    confidence: float
    position: Optional[EEPositionSchema] = Field(
        None, description="카메라 optical frame 3D 좌표; depth 실패 시 null"
    )

    model_config = _example({
        "cup_id": 0,
        "yaw": 3.10,
        "grip_pixel": {"x": 425.0, "y": 306.0},
        "confidence": 0.91,
        "position": {"x": 0.012, "y": -0.034, "z": 0.41},
    })


class FallenCupStateResponse(BaseModel):
    """GET /api/robot/fallen-cup/state 응답.

    인식 데이터는 2초 이상 갱신이 없으면 stale로 간주되어 null/[]로 내려간다.
    """

    detection_running: bool = Field(..., description="fallen_cup_detect 서비스 실행 여부")
    count: int = Field(..., description="감지된 fallen cup 수 (multi-cup 토픽 기준)")
    cups: list[FallenCupItem] = Field(default=[], description="감지된 컵 목록")
    pose2d: Optional[FallenCupPose2D] = Field(None, description="단일 컵 2D 인식 결과")
    grasp_pose: Optional[FallenCupGraspPose] = Field(None, description="단일 컵 3D grasp 좌표")

    model_config = _example({
        "detection_running": True,
        "count": 1,
        "cups": [{
            "cup_id": 0,
            "yaw": 3.10,
            "grip_pixel": {"x": 425.0, "y": 306.0},
            "confidence": 0.91,
            "position": {"x": 0.012, "y": -0.034, "z": 0.41},
        }],
        "pose2d": None,
        "grasp_pose": None,
    })


class MouthUpCupGraspPose(BaseModel):
    """3D grasp 좌표 (/mouth_up_cup/grasp_pose, 카메라 optical frame)."""

    frame_id: str
    position: EEPositionSchema
    orientation: dict = Field(..., description="quaternion {x, y, z, w}")

    model_config = _example({
        "frame_id": "camera_color_optical_frame",
        "position": {"x": 0.012, "y": -0.034, "z": 0.41},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    })


class MouthUpCupStateResponse(BaseModel):
    """GET /api/robot/mouth-up-cup/state 응답.

    인식 데이터는 2초 이상 갱신이 없으면 stale로 간주되어 null로 내려간다.
    """

    detection_running: bool = Field(..., description="mouth_up_cup_detect 서비스 실행 여부")
    detected: bool = Field(..., description="최근(2초 내) mouth-up grasp 좌표 수신 여부")
    grasp_pose: Optional[MouthUpCupGraspPose] = Field(None, description="mouth-up 3D grasp 좌표")

    model_config = _example({
        "detection_running": True,
        "detected": False,
        "grasp_pose": None,
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
    그리퍼 Z를 산출한다. ``z`` / ``cup_top_z`` / ``nested_count`` 중
    어느 것도 주지 않으면 ``nested``(기본 1)로 Z를 산출한다.
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
    nested: int = Field(
        1, ge=1,
        description="source nest 에 남은 컵 수 (1=맨 아래 한 개). nested_count "
        "미지정 시 이 값으로 Z 산출 (pick_z_base + (nested-1)*nest_inc). 기본 1.",
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
    nested: int = Field(
        1,
        ge=1,
        description="source nest 에 남은 컵 수 (1=맨 아래 한 개). "
        "pick_z = pick_z + (nested-1)*nest_inc 로 위 컵부터 집는다. "
        "기본 1 → 기존 동작과 동일.",
    )

    model_config = _example({"x": 0.40, "y": 0.10, "slot": "1l", "nested": 1})


class PyramidSkillResponse(BaseModel):
    success: bool
    skill: str = "pyramid"
    detail: str = ""

    model_config = _example({
        "success": True,
        "skill": "pyramid",
        "detail": "slot=1l pick=(0.40,0.10,0.313) place=(0.50,-0.079,0.323)",
    })


# ── Unstack Skill ─────────────────────────────────────────────────────────────

class UnstackSkillRequest(BaseModel):
    """POST /api/robot/skill/unstack 본문. pick 할 slot + 목적지 nest XY.

    pyramid skill 의 역동작: 피라미드 slot 에 놓인 컵을 집어 목적지 (x, y)
    에 nested 컬럼으로 쌓는다. pick 좌표(slot 절대 위치)·pick_z 는 서버의
    /api/robot/config/pyramid 캐시에서 자동으로 가져온다.

    피라미드는 위에서부터 해체해야 하므로 호출 순서는 3m → 2r → 2l →
    1r → 1m → 1l 이어야 한다 (호출자 책임).
    """

    slot: PyramidSlotKey = Field(
        ..., description="집을 슬롯 키: 3m (top) → 2r/2l (mid) → 1r/1m/1l (bottom)"
    )
    x: float = Field(..., description="목적지 nest 중앙 X (base_link, m)")
    y: float = Field(..., description="목적지 nest 중앙 Y (base_link, m)")
    nested: int = Field(
        1,
        ge=1,
        description="이 컵을 놓은 뒤 목적지 컬럼 높이 (1=맨 아래 첫 컵). "
        "place_z = pick_z + (nested-1)*nest_inc 로 컵이 위로 nesting 된다.",
    )

    model_config = _example({"slot": "3m", "x": 0.40, "y": 0.10, "nested": 1})


class UnstackSkillResponse(BaseModel):
    success: bool
    skill: str = "unstack"
    detail: str = ""

    model_config = _example({
        "success": True,
        "skill": "unstack",
        "detail": "slot=3m pick=(0.50,0.000,0.513) place=(0.40,0.10,0.313)",
    })


# ── Unstack-All (full teardown) Skill ─────────────────────────────────────────

class UnstackAllSkillRequest(BaseModel):
    """POST /api/robot/skill/unstack_all 본문. 목적지 nest XY 만 받음.

    ``script/unstack.sh`` 의 서버측 스킬화: 피라미드 6 컵을 위에서부터
    (3m → 2r → 2l → 1r → 1m → 1l) 순서로 모두 집어 목적지 (x, y) 한 곳에
    nested 컬럼으로 쌓는다. slot 별 pick 좌표·pick_z 는 서버
    /api/robot/config/pyramid 캐시에서 자동으로 가져오므로 본문에 없다.

    각 단계는 일시적 실패(로봇 모션 409 / 터널 blip)에 대해 ``max_retry``
    회까지 ``retry_delay`` 초 간격으로 재시도한다 (unstack.sh 와 동일).
    """

    x: float = Field(0.400, description="목적지 nest 중앙 X (base_link, m)")
    y: float = Field(0.100, description="목적지 nest 중앙 Y (base_link, m)")
    max_retry: int = Field(
        5, ge=1, le=20, description="단계별 재시도 횟수 (일시적 실패 대응)"
    )
    retry_delay: float = Field(
        3.0, ge=0.0, le=30.0, description="재시도 간 대기(초)"
    )

    model_config = _example({"x": 0.40, "y": 0.10})


class UnstackAllStep(BaseModel):
    """전체 해체 시퀀스의 단계별 결과 (슬롯 1 개당 1 entry)."""

    slot: PyramidSlotKey
    nested: int = Field(..., description="이 컵을 놓은 뒤 목적지 컬럼 높이 (1=맨 아래)")
    success: bool
    attempts: int = Field(..., description="이 단계에 소요된 시도 횟수")
    detail: str = ""


class UnstackAllSkillResponse(BaseModel):
    """전체 피라미드 해체 결과.

    ``success`` 는 6 컵 전부 해체 성공 여부. 도중에 한 단계가 모든 재시도
    후에도 실패하면 시퀀스를 멈추고 ``success=False`` + ``completed`` (성공한
    컵 수) 로 반환한다 (부분 진행 보고용 — 5xx 를 던지지 않는다).
    """

    success: bool
    skill: str = "unstack_all"
    dest: PyramidConfigCenter
    total: int = Field(6, description="해체 대상 컵 총 수")
    completed: int = Field(..., description="성공적으로 해체·적재한 컵 수 (0~6)")
    detail: str = ""
    steps: list[UnstackAllStep] = Field(default_factory=list)

    model_config = _example({
        "success": True,
        "skill": "unstack_all",
        "dest": {"x": 0.40, "y": 0.10},
        "total": 6,
        "completed": 6,
        "detail": "피라미드 해체 완료 (6/6) -> nest (x=0.400, y=0.100)",
        "steps": [
            {"slot": "3m", "nested": 1, "success": True, "attempts": 1, "detail": ""},
        ],
    })


# ── Scan Skill ────────────────────────────────────────────────────────────────

class ScanSkillResponse(BaseModel):
    """Response for POST /api/robot/skill/scan.

    스캔은 인자가 없는 단일 스킬이라 별도의 Request 모델은 없다. ROS 2
    skill_api_node 의 ``ScanSkill`` 이 ``scan.launch.py`` 를 실행하여 양쪽
    두 방향(pos1, pos2) 을 거쳐 초기 위치로 복귀한다.
    """

    success: bool
    skill: str = "scan"
    detail: str = ""

    model_config = _example({
        "success": True, "skill": "scan", "detail": "",
    })


class ScanSquareSkillResponse(BaseModel):
    """Response for POST /api/robot/skill/scan_square.

    인자 없는 단일 스킬. ROS 2 skill_api_node 의 ``ScanSkill`` 이
    ``scan_square.launch.py`` 를 실행하여 카메라를 하향 고정한 채 base_link
    XY 평면의 사각형 네 꼭짓점을 (HOME EE 높이에서) 순회한 뒤 시작 위치로
    복귀한다.
    """

    success: bool
    skill: str = "scan_square"
    detail: str = ""

    model_config = _example({
        "success": True, "skill": "scan_square", "detail": "",
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
