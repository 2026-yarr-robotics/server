"""FastAPI application entry point."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import AppSettings
from .domains.fallen_cup import FallenCupDomain
from .domains.mouth_up_cup import MouthUpCupDomain
from .domains.handineye import HandInEyeDomain
from .domains.handtoeye import HandToEyeDomain
from .domains.robot import RobotDomain
from .ros.bridge import RosBridge, connect_bridge, disconnect_bridge
from .ros.launch import LaunchManager
from .routers import dashboard, handineye, handtoeye, robot
from .services.calibration import CalibrationStore
from .services.camera import CameraManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

settings = AppSettings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bridge = await connect_bridge(settings.rosbridge)
    launcher = LaunchManager(
        settings.workspace,
        agent_url=os.getenv("BRINGUP_AGENT_URL"),
    )
    launcher.start_agent_reconcile()
    calibration_store = CalibrationStore(settings.workspace.config_dir)
    camera_mgr = CameraManager(settings.rosbridge)

    robot_domain = RobotDomain(
        bridge,
        launcher,
        settings.robot.joint_states,
        workspace_limits=settings.workspace_limits,
        robot_home=settings.robot_home,
        skill_api_url=settings.skill_api.url,
        pyramid_state_path=settings.state_dir / "pyramid_config.json",
    )
    robot_domain.subscribe()

    # hand = eye-in-hand (EE-mounted) · exo = eye-to-hand (fixed/external)
    handineye_domain = HandInEyeDomain(
        bridge,
        calibration_store,
        settings.cameras.hand_info,
        settings.cameras.hand_color,
    )

    handtoeye_domain = HandToEyeDomain(
        bridge,
        calibration_store,
        settings.cameras.exo_info,
        settings.cameras.exo_color,
    )

    camera_mgr.subscribe_all({
        "hand": settings.cameras.hand_color,
        "exo": settings.cameras.exo_color,
    })

    bridge.subscribe(
        settings.cameras.hand_info,
        "sensor_msgs/msg/CameraInfo",
        handineye_domain.on_camera_info,
    )
    bridge.subscribe(
        settings.cameras.exo_info,
        "sensor_msgs/msg/CameraInfo",
        handtoeye_domain.on_camera_info,
    )

    fallen_cup_domain = FallenCupDomain(
        bridge,
        launcher,
        config=settings.fallen_cup,
        topics=settings.fallen_cup_topics,
    )
    fallen_cup_domain.subscribe()

    mouth_up_cup_domain = MouthUpCupDomain(
        bridge,
        launcher,
        config=settings.mouth_up_cup,
        topics=settings.mouth_up_cup_topics,
    )
    mouth_up_cup_domain.subscribe()

    robot.set_robot_domain(robot_domain)
    robot.set_fallen_cup_domain(fallen_cup_domain)
    robot.set_mouth_up_cup_domain(mouth_up_cup_domain)
    handineye.set_handineye_domain(handineye_domain)
    handtoeye.set_handtoeye_domain(handtoeye_domain)
    dashboard.set_dashboard_deps(robot_domain, camera_mgr, launcher)

    logger.info("cup_stack server started")
    yield

    await launcher.shutdown_all()
    await disconnect_bridge()
    RosBridge.reset()
    logger.info("cup_stack server stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Cup Stack Server API",
        version="0.1.0",
        description=(
            "Doosan M0609 컵 스태킹 로봇 제어 서버.\n\n"
            "서비스별 포트: **robot** 8001 · **handineye** 8002 · **handtoeye** 8003"
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(robot.router)
    app.include_router(handineye.router)
    app.include_router(handtoeye.router)
    app.include_router(dashboard.router)

    return app


app = create_app()


def main() -> None:
    uvicorn_config = {
        "host": settings.server.host,
        "port": settings.server.port,
    }
    if settings.server.ssl_certfile and settings.server.ssl_keyfile:
        uvicorn_config["ssl_certfile"] = settings.server.ssl_certfile
        uvicorn_config["ssl_keyfile"] = settings.server.ssl_keyfile

    uvicorn.run(
        "server.main:app",
        **uvicorn_config,
    )


if __name__ == "__main__":
    main()
