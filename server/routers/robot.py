"""REST API endpoints for robot + gripper control."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domains.robot import RobotDomain
from ..ros.launch import ALL_COMMANDS
from ..schemas import (
    BringupRequest,
    EEPositionSchema,
    GripperRequest,
    GripperResponse,
    MoveRequest,
    MoveResponse,
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


def set_robot_domain(domain: RobotDomain) -> None:
    global robot_domain
    robot_domain = domain


def _get_domain() -> RobotDomain:
    if robot_domain is None:
        raise HTTPException(status_code=503, detail="Robot domain not initialized")
    return robot_domain


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

    domain = _get_domain()
    try:
        lines = await domain.get_log(name, tail)
        return {"name": name, "log": lines}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/position", response_model=EEPositionSchema)
async def get_ee_position() -> dict:
    """Return last known end-effector position (last commanded position)."""
    pos = _get_domain().get_ee_position()
    if pos is None:
        raise HTTPException(
            status_code=404,
            detail="Position not yet known — issue a move command first",
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
