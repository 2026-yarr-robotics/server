"""Robot + gripper service entry point."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from ..access_log import APIAccessLogMiddleware
from ..config import AppSettings
from ..domains.fallen_cup import FallenCupDomain
from ..domains.mouth_up_cup import MouthUpCupDomain
from ..domains.robot import RobotDomain
from ..ros.bridge import RosBridge, connect_bridge, disconnect_bridge
from ..ros.launch import LaunchManager
from ..routers.robot import (
    router as robot_router,
    set_fallen_cup_domain,
    set_mouth_up_cup_domain,
    set_robot_domain,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

from ..config import (
    FallenCupConfig as _FCC,
    MouthUpCupConfig as _MUC,
    RosBridgeConfig as _RBC,
)

settings = AppSettings()
settings.rosbridge = _RBC(
    host=os.getenv("ROSBRIDGE_HOST", settings.rosbridge.host),
    port=int(os.getenv("ROSBRIDGE_PORT", str(settings.rosbridge.port))),
)
settings.fallen_cup = _FCC(
    weights_path=os.getenv("FALLEN_CUP_WEIGHTS", settings.fallen_cup.weights_path),
    conf=settings.fallen_cup.conf,
    imgsz=settings.fallen_cup.imgsz,
    use_depth=settings.fallen_cup.use_depth,
    device=os.getenv("FALLEN_CUP_DEVICE", settings.fallen_cup.device),
)
settings.mouth_up_cup = _MUC(
    weights_path=os.getenv("MOUTH_UP_CUP_WEIGHTS", settings.mouth_up_cup.weights_path),
    conf=settings.mouth_up_cup.conf,
    imgsz=settings.mouth_up_cup.imgsz,
    target_class_name=settings.mouth_up_cup.target_class_name,
    device=os.getenv("MOUTH_UP_CUP_DEVICE", settings.mouth_up_cup.device),
)

_domain: RobotDomain | None = None
_launcher: LaunchManager | None = None
_fallen_cup_domain: FallenCupDomain | None = None
_mouth_up_cup_domain: MouthUpCupDomain | None = None


def create_app() -> FastAPI:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _domain, _launcher, _fallen_cup_domain, _mouth_up_cup_domain

        _launcher = LaunchManager(
            settings.workspace,
            agent_url=os.getenv("BRINGUP_AGENT_URL"),
        )
        _launcher.start_agent_reconcile()

        try:
            bridge = await connect_bridge(settings.rosbridge)
        except Exception:
            logger.warning(
                "rosbridge unavailable at %s:%d; starting without ROS. "
                "Docs/OpenAPI are served; ROS-backed endpoints return 503 "
                "until the service is restarted with rosbridge up.",
                settings.rosbridge.host,
                settings.rosbridge.port,
                exc_info=True,
            )
            bridge = None

        if bridge is not None:
            _domain = RobotDomain(
                bridge,
                _launcher,
                settings.robot.joint_states,
                workspace_limits=settings.workspace_limits,
                robot_home=settings.robot_home,
                config_dir=settings.workspace.config_dir,
                camera_info_topic=settings.cameras.hand_info,
                depth_topic=settings.cameras.hand_depth,
                skill_api_url=os.getenv("SKILL_API_URL", settings.skill_api.url),
                pyramid_state_path=settings.state_dir / "pyramid_config.json",
            )
            _domain.subscribe()
            set_robot_domain(_domain)

            _fallen_cup_domain = FallenCupDomain(
                bridge,
                _launcher,
                config=settings.fallen_cup,
                topics=settings.fallen_cup_topics,
            )
            _fallen_cup_domain.subscribe()
            set_fallen_cup_domain(_fallen_cup_domain)

            _mouth_up_cup_domain = MouthUpCupDomain(
                bridge,
                _launcher,
                config=settings.mouth_up_cup,
                topics=settings.mouth_up_cup_topics,
            )
            _mouth_up_cup_domain.subscribe()
            set_mouth_up_cup_domain(_mouth_up_cup_domain)

        logger.info("robot service started on port %d", settings.ports.robot)
        yield

        await _launcher.shutdown_all()
        try:
            await disconnect_bridge()
        except Exception:
            logger.warning("error during rosbridge disconnect", exc_info=True)
        RosBridge.reset()
        logger.info("robot service stopped")

    app = FastAPI(
        title="cup_stack Robot Service",
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "Doosan M0609 컵 스태킹 로봇 제어 (robot 서비스).\n\n"
            "### WebSocket 엔드포인트\n"
            "OpenAPI/Swagger는 WebSocket을 표기하지 않습니다. 이 서비스의 소켓:\n\n"
            "- `ws://<host>/ws/robot/state` — 로봇 상태 스트림. 페이로드는 "
            "`GET /api/robot/status`와 동일(`RobotStatusResponse`), 약 100ms"
            "(10Hz) 주기 push.\n"
            "- `ws://<host>/ws/task/log` — 실행 태스크 로그 증분 스트림 "
            "(`{task, status, log[]}`), 약 500ms 주기.\n\n"
            "프로덕션은 `wss://<도메인>/ws/robot/state` 처럼 nginx/터널 경유.\n"
        ),
        # Serve docs under the /api/robot prefix so nginx's existing
        # `location /api/robot/` block proxies them to this service.
        docs_url="/api/robot/docs",
        redoc_url="/api/robot/redoc",
        openapi_url="/api/robot/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(APIAccessLogMiddleware)

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

        last_task_name: str | None = None
        log_cursor: int = 0

        try:
            while True:
                active = _launcher.active_action_task
                task = active
                if task is None:
                    bringup = _launcher.bringup_task
                    if bringup is not None and bringup.status.value == "running":
                        task = bringup

                if task is None:
                    last_task_name = None
                    log_cursor = 0
                    await ws.send_json({"task": None, "status": "idle", "log": []})
                else:
                    if task.name != last_task_name:
                        last_task_name = task.name
                        log_cursor = len(task.log_lines)

                    log_cursor = min(log_cursor, len(task.log_lines))
                    new_lines = task.log_lines[log_cursor:]
                    log_cursor = len(task.log_lines)

                    if new_lines:
                        await ws.send_json({
                            "task": task.name,
                            "status": task.status.value,
                            "log": new_lines,
                        })
                    else:
                        await ws.send_json({
                            "task": task.name,
                            "status": task.status.value,
                            "log": [],
                        })

                await asyncio.sleep(0.5)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("task log ws error")
            await ws.close(code=1011)

    @app.websocket("/ws/agent/log")
    async def ws_agent_log(ws: WebSocket) -> None:
        # Streams the cup_stack_agent LLM-loop logs (llm_node / plan_executor /
        # pick_node / goal_state_publisher). The agent writes them to the HOST
        # filesystem; this service is containerised, so it proxies the host
        # bringup-agent's GET /agent/log (reusing the configured agent_url) and
        # forwards new lines ~1Hz. Mirrors the /ws/task/log streaming pattern.
        await ws.accept()
        agent_url = _launcher.agent_url if _launcher is not None else None
        if not agent_url:
            await ws.close(code=503, reason="No bringup agent (BRINGUP_AGENT_URL)")
            return

        loop = asyncio.get_running_loop()
        cursor = 0.0
        limit = 80  # first fetch: send recent tail for context

        try:
            while True:
                url = f"{agent_url}/agent/log?since={cursor}"
                if limit:
                    url += f"&limit={limit}"

                def _fetch(u: str = url) -> dict:
                    with urllib.request.urlopen(u, timeout=5) as resp:
                        return json.loads(resp.read())

                try:
                    data = await loop.run_in_executor(None, _fetch)
                    cursor = data.get("cursor", cursor)
                    lines = data.get("lines", [])
                    if lines or limit:
                        await ws.send_json(
                            {"run_id": data.get("run_id"), "lines": lines}
                        )
                    limit = 0  # subsequent fetches are incremental
                except Exception as exc:  # noqa: BLE001 — keep the socket alive
                    logger.warning("agent log poll error: %s", exc)

                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("agent log ws error")
            await ws.close(code=1011)

    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "server.entrypoints.robot:app",
        host="0.0.0.0",
        port=settings.ports.robot,
        # See handtoeye: disable buggy server-side WS keepalive that tears the
        # dashboard camera connection down under send back-pressure.
        ws_ping_interval=None,
    )


if __name__ == "__main__":
    main()
