"""Robot + gripper service entry point."""

from __future__ import annotations

import asyncio
import logging
import os

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from ..config import AppSettings
from ..domains.cup_detection import CupDetectionDomain
from ..domains.robot import RobotDomain
from ..ros.bridge import RosBridge, connect_bridge, disconnect_bridge
from ..ros.launch import LaunchManager
from ..routers.robot import router as robot_router, set_cup_detection_domain, set_robot_domain

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from ..config import RosBridgeConfig as _RBC

settings = AppSettings()
settings.rosbridge = _RBC(
    host=os.getenv("ROSBRIDGE_HOST", settings.rosbridge.host),
    port=int(os.getenv("ROSBRIDGE_PORT", str(settings.rosbridge.port))),
)

_domain: RobotDomain | None = None
_launcher: LaunchManager | None = None
_cup_domain: CupDetectionDomain | None = None


def create_app() -> FastAPI:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _domain, _launcher, _cup_domain

        bridge = await connect_bridge(settings.rosbridge)
        _launcher = LaunchManager(
            settings.workspace,
            agent_url=os.getenv("BRINGUP_AGENT_URL"),
        )
        _domain = RobotDomain(
            bridge,
            _launcher,
            settings.robot.joint_states,
        )
        _domain.subscribe()
        set_robot_domain(_domain)

        _cup_domain = CupDetectionDomain(bridge, _launcher)
        _cup_domain.subscribe()
        set_cup_detection_domain(_cup_domain)

        logger.info("robot service started on port %d", settings.ports.robot)
        yield

        await _launcher.shutdown_all()
        await disconnect_bridge()
        RosBridge.reset()
        logger.info("robot service stopped")

    app = FastAPI(
        title="cup_stack Robot Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(robot_router)

    @app.websocket("/ws/robot/state")
    async def ws_robot_state(ws: WebSocket) -> None:
        await ws.accept()
        if _domain is None:
            await ws.close(code=503, reason="Not initialized")
            return
        try:
            while True:
                await ws.send_json(_domain.get_status())
                await asyncio.sleep(0.1)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("robot state ws error")
            await ws.close(code=1011)

    @app.websocket("/ws/task/log")
    async def ws_task_log(ws: WebSocket) -> None:
        await ws.accept()
        if _launcher is None:
            await ws.close(code=503, reason="Not initialized")
            return
        try:
            while True:
                active = _launcher.active_action_task
                if active is not None:
                    await ws.send_json({
                        "task": active.name,
                        "status": active.status.value,
                        "log": active.log_lines[-5:],
                    })
                else:
                    bringup = _launcher.bringup_task
                    if bringup is not None and bringup.status.value == "running":
                        await ws.send_json({
                            "task": bringup.name,
                            "status": bringup.status.value,
                            "log": bringup.log_lines[-5:],
                        })
                    else:
                        await ws.send_json({"task": None, "status": "idle", "log": []})
                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("task log ws error")
            await ws.close(code=1011)

    @app.websocket("/ws/cups")
    async def ws_cups(ws: WebSocket) -> None:
        await ws.accept()
        if _cup_domain is None:
            await ws.close(code=503, reason="Not initialized")
            return
        try:
            while True:
                await ws.send_json(_cup_domain.get_cups())
                await asyncio.sleep(0.1)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("cups ws error")
            await ws.close(code=1011)

    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "server.entrypoints.robot:app",
        host="0.0.0.0",
        port=settings.ports.robot,
    )


if __name__ == "__main__":
    main()
