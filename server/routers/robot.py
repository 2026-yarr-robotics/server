"""REST API endpoints for robot + gripper control."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..domains.robot import RobotDomain
from ..ros.launch import ALL_COMMANDS

router = APIRouter(prefix="/api/robot", tags=["robot"])

robot_domain: RobotDomain | None = None


def set_robot_domain(domain: RobotDomain) -> None:
    global robot_domain
    robot_domain = domain


def _get_domain() -> RobotDomain:
    if robot_domain is None:
        raise HTTPException(status_code=503, detail="Robot domain not initialized")
    return robot_domain


@router.get("/status")
async def get_status() -> dict[str, Any]:
    return _get_domain().get_status()


@router.post("/bringup")
async def start_bringup(body: dict[str, Any]) -> dict[str, Any]:
    mode = body.get("mode", "sim")
    command = f"bringup_{mode}"
    if command not in ALL_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {mode}")

    args: dict[str, Any] = {}
    if mode == "real":
        args["ip"] = body.get("ip", "192.168.1.100")

    domain = _get_domain()
    try:
        return await domain.start_task(command, args)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/task/start")
async def start_task(body: dict[str, Any]) -> dict[str, Any]:
    task_name = body.get("task")
    if not task_name or task_name not in ALL_COMMANDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid task. Choose from: {sorted(ALL_COMMANDS)}",
        )

    args = body.get("args", {})
    domain = _get_domain()
    try:
        return await domain.start_task(task_name, args)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/task/stop")
async def stop_task(body: dict[str, Any]) -> dict[str, Any]:
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Missing 'name' field")

    domain = _get_domain()
    try:
        return await domain.stop_task(name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/task/log")
async def get_task_log(name: str = "", tail: int = 50) -> dict[str, Any]:
    if not name:
        raise HTTPException(status_code=400, detail="Missing 'name' query param")

    domain = _get_domain()
    try:
        lines = await domain.get_log(name, tail)
        return {"name": name, "log": lines}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/workspace/limits")
async def get_workspace_limits() -> dict[str, Any]:
    """Get workspace safe zone limits."""
    return _get_domain().move_limits


@router.post("/move")
async def move_robot(body: dict[str, Any]) -> dict[str, Any]:
    """Move robot end-effector to specified position."""
    x = body.get("x")
    y = body.get("y")
    z = body.get("z")
    mode = body.get("mode", "absolute")

    if x is None or y is None or z is None:
        raise HTTPException(status_code=400, detail="Missing required fields: x, y, z")

    if mode not in ("absolute", "relative"):
        raise HTTPException(status_code=400, detail="mode must be 'absolute' or 'relative'")

    domain = _get_domain()
    try:
        result = await domain.move_to(float(x), float(y), float(z), mode)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
