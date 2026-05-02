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

settings = AppSettings()
settings.rosbridge.host = os.getenv("ROSBRIDGE_HOST", settings.rosbridge.host)
settings.rosbridge.port = int(os.getenv("ROSBRIDGE_PORT", str(settings.rosbridge.port)))

_domain: HandToEyeDomain | None = None
_camera: CameraStream | None = None


def create_app() -> FastAPI:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _domain, _camera

        bridge = await connect_bridge(settings.rosbridge)
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

        await disconnect_bridge()
        RosBridge.reset()
        logger.info("handtoeye service stopped")

    app = FastAPI(
        title="cup_stack Hand-to-Eye Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
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
