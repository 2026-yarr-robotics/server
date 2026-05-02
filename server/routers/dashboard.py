"""WebSocket endpoints for real-time dashboard streaming."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..domains.robot import RobotDomain
from ..ros.launch import LaunchManager
from ..services.camera import CameraManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

robot_domain: RobotDomain | None = None
camera_manager: CameraManager | None = None
launch_manager: LaunchManager | None = None


def set_dashboard_deps(
    robot: RobotDomain,
    cameras: CameraManager,
    launcher: LaunchManager,
) -> None:
    global robot_domain, camera_manager, launch_manager
    robot_domain = robot
    camera_manager = cameras
    launch_manager = launcher


@router.websocket("/ws/robot/state")
async def ws_robot_state(ws: WebSocket) -> None:
    await ws.accept()
    if robot_domain is None:
        await ws.close(code=503, reason="Robot domain not initialized")
        return

    try:
        while True:
            data = robot_domain.get_status()
            await ws.send_json(data)
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Error in robot state WebSocket")
        await ws.close(code=1011)


@router.websocket("/ws/camera/{camera_name}")
async def ws_camera_stream(ws: WebSocket, camera_name: str) -> None:
    await ws.accept()
    if camera_manager is None:
        await ws.close(code=503, reason="Camera manager not initialized")
        return

    stream = camera_manager._streams.get(camera_name)

    if stream is None:
        await ws.close(code=404, reason=f"Camera '{camera_name}' not found")
        return

    try:
        async for frame in stream.frames():
            await ws.send_bytes(frame)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Error in camera WebSocket for %s", camera_name)
        await ws.close(code=1011)


@router.websocket("/ws/task/log")
async def ws_task_log(ws: WebSocket) -> None:
    await ws.accept()
    if launch_manager is None:
        await ws.close(code=503, reason="Launch manager not initialized")
        return

    try:
        while True:
            active = launch_manager.active_task
            if active is not None:
                logs = active.log_lines[-5:]
                await ws.send_json({
                    "task": active.name,
                    "status": active.status.value,
                    "log": logs,
                })
            else:
                await ws.send_json({"task": None, "status": "idle", "log": []})
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Error in task log WebSocket")
        await ws.close(code=1011)
