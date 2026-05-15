"""REST API endpoints for robot + gripper control."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domains.cup_detection import CupDetectionDomain
from ..domains.robot import RobotDomain
from ..ros.launch import ALL_COMMANDS
from ..schemas import (
    BringupRequest,
    CupDetectionFrame,
    CupTriggerRequest,
    EEPositionSchema,
    GripperRequest,
    GripperResponse,
    MoveRequest,
    MoveResponse,
    PixelToWorldResponse,
    RobotStatusResponse,
    TaskLogResponse,
    TaskStartedResponse,
    TaskStartRequest,
    TaskStopRequest,
    TaskStoppedResponse,
    WorkspaceLimitsResponse,
)

router = APIRouter(prefix="/api/robot", tags=["robot"])

robot_domain: RobotDomain | None = None
cup_detection_domain: CupDetectionDomain | None = None


def set_robot_domain(domain: RobotDomain) -> None:
    global robot_domain
    robot_domain = domain


def set_cup_detection_domain(domain: CupDetectionDomain) -> None:
    global cup_detection_domain
    cup_detection_domain = domain


def _get_domain() -> RobotDomain:
    if robot_domain is None:
        raise HTTPException(status_code=503, detail="Robot domain not initialized")
    return robot_domain


def _get_cup_domain() -> CupDetectionDomain:
    if cup_detection_domain is None:
        raise HTTPException(status_code=503, detail="Cup detection domain not initialized")
    return cup_detection_domain


@router.get("/status", response_model=RobotStatusResponse)
async def get_status() -> dict:
    return _get_domain().get_status()


@router.post("/bringup", response_model=TaskStartedResponse)
async def start_bringup(body: BringupRequest) -> dict:
    mode = body.mode
    command = f"bringup_{mode}"
    if command not in ALL_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {mode}")

    args: dict = {}
    if mode == "real":
        args["ip"] = body.ip

    domain = _get_domain()
    try:
        return await domain.start_task(command, args)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/task/start", response_model=TaskStartedResponse)
async def start_task(body: TaskStartRequest) -> dict:
    task_name = body.task
    if task_name not in ALL_COMMANDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid task. Choose from: {sorted(ALL_COMMANDS)}",
        )

    domain = _get_domain()
    try:
        return await domain.start_task(task_name, body.args)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/task/stop", response_model=TaskStoppedResponse)
async def stop_task(body: TaskStopRequest) -> dict:
    domain = _get_domain()
    try:
        return await domain.stop_task(body.name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/task/log", response_model=TaskLogResponse)
async def get_task_log(name: str = "", tail: int = 50) -> dict:
    if not name:
        raise HTTPException(status_code=400, detail="Missing 'name' query param")
    if not (1 <= tail <= 500):
        raise HTTPException(status_code=400, detail="tail must be between 1 and 500")

    domain = _get_domain()
    try:
        lines = await domain.get_log(name, tail)
        return {"name": name, "log": lines}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/position", response_model=EEPositionSchema)
async def get_ee_position() -> dict:
    """Return the current end-effector position from the robot's /ee_pose.

    No fallback to the last commanded target: if /ee_pose has not been
    received the position is reported as unavailable (404).
    """
    pos = _get_domain().get_ee_position()
    if pos is None:
        raise HTTPException(
            status_code=404,
            detail="End-effector pose unavailable — /ee_pose not received (is bringup running?)",
        )
    return pos


@router.get("/workspace/limits", response_model=WorkspaceLimitsResponse)
async def get_workspace_limits() -> dict:
    return _get_domain().move_limits


@router.post("/gripper", response_model=GripperResponse)
async def gripper_control(body: GripperRequest) -> dict:
    command = body.command.strip().lower()
    if command not in ("open", "close"):
        raise HTTPException(status_code=400, detail="command must be 'open' or 'close'")
    domain = _get_domain()
    try:
        return await domain.gripper_control(command)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/move", response_model=MoveResponse)
async def move_robot(body: MoveRequest) -> dict:
    domain = _get_domain()
    try:
        return await domain.move_to(body.x, body.y, body.z, body.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/pixel-to-world", response_model=PixelToWorldResponse)
async def pixel_to_world(px: int, py: int) -> dict:
    domain = _get_domain()
    try:
        return domain.pixel_to_world(px, py)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/cups", response_model=CupDetectionFrame)
async def get_cups() -> dict:
    return _get_cup_domain().get_cups()


@router.post("/cups/trigger", response_model=TaskStartedResponse)
async def trigger_cup_task(body: CupTriggerRequest) -> dict:
    domain = _get_cup_domain()
    try:
        return await domain.trigger_task(body.cup_id, body.task)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        detail = str(e)
        if "cup_detection task is not running" in detail:
            raise HTTPException(status_code=503, detail=detail)
        raise HTTPException(status_code=409, detail=detail)
