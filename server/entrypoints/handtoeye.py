"""Hand-to-eye calibration service entry point."""

from __future__ import annotations

import asyncio
import logging
import os

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from ..config import AppSettings
from ..domains.handtoeye import HandToEyeDomain
from ..ros.bridge import RosBridge, connect_bridge, disconnect_bridge
from ..routers.handtoeye import router as handtoeye_router, set_handtoeye_domain
from ..services.calibration import CalibrationStore
from ..services.camera import CameraStream

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

_domain: HandToEyeDomain | None = None
_camera: CameraStream | None = None


def create_app() -> FastAPI:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _domain, _camera

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
            calib_store = CalibrationStore(settings.workspace.config_dir)
            _domain = HandToEyeDomain(
                bridge,
                calib_store,
                settings.cameras.handtoeye_info,
                settings.cameras.handtoeye_color,
            )
            set_handtoeye_domain(_domain)

            bridge.subscribe(
                settings.cameras.handtoeye_info,
                "sensor_msgs/msg/CameraInfo",
                _domain.on_camera_info,
            )

            _camera = CameraStream(bridge, settings.cameras.handtoeye_color)
            _camera.subscribe()

        logger.info(
            "handtoeye service started on port %d", settings.ports.handtoeye,
        )
        yield

        try:
            await disconnect_bridge()
        except Exception:
            logger.warning("error during rosbridge disconnect", exc_info=True)
        RosBridge.reset()
        logger.info("handtoeye service stopped")

    app = FastAPI(
        title="cup_stack Hand-to-Eye Service",
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "Hand-to-Eye 카메라/캘리브레이션 서비스.\n\n"
            "### WebSocket 엔드포인트\n"
            "OpenAPI/Swagger는 WebSocket을 표기하지 않습니다. 이 서비스의 소켓:\n\n"
            "- `ws://<host>/ws/camera/handtoeye` — 카메라 프레임 스트림 "
            "(바이너리 JPEG, 프레임 도착 시 push).\n"
        ),
        # Serve docs under the /api/handtoeye prefix so nginx's existing
        # `location /api/handtoeye/` block proxies them to this service.
        docs_url="/api/handtoeye/docs",
        redoc_url="/api/handtoeye/redoc",
        openapi_url="/api/handtoeye/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(handtoeye_router)

    @app.websocket("/ws/camera/handtoeye")
    async def ws_camera(ws: WebSocket) -> None:
        await ws.accept()
        if _camera is None:
            await ws.close(code=503, reason="Not initialized")
            return
        try:
            async for frame in _camera.frames():
                await ws.send_bytes(frame)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("handtoeye camera ws error")
            await ws.close(code=1011)

    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "server.entrypoints.handtoeye:app",
        host="0.0.0.0",
        port=settings.ports.handtoeye,
    )


if __name__ == "__main__":
    main()
