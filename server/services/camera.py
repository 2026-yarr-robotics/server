"""Camera streaming service: subscribes via rosbridge and serves MJPG frames."""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any, AsyncIterator, Callable

import cv2
import numpy as np

from ..ros.bridge import RosBridge

logger = logging.getLogger(__name__)


class CameraStream:
    """Subscribes to a compressed image topic and yields MJPG frames."""

    def __init__(
        self,
        bridge: RosBridge,
        topic: str,
    ) -> None:
        self._bridge = bridge
        self._topic = topic
        self._latest_frame: bytes = b""
        self._subscribed = False
        self._subscribers: list[asyncio.Event] = []

    def subscribe(self) -> None:
        if self._subscribed:
            return
        self._bridge.subscribe(
            self._topic,
            "sensor_msgs/msg/CompressedImage",
            self._on_frame,
            throttle_rate=50,
        )
        self._subscribed = True
        logger.info("Camera subscribed to %s", self._topic)

    def _on_frame(self, msg: dict[str, Any]) -> None:
        data_b64 = msg.get("data", "")
        if not data_b64:
            return
        raw = base64.b64decode(data_b64)
        nparr = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is not None:
            _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            self._latest_frame = jpeg.tobytes()
            for event in self._subscribers:
                event.set()

    async def frames(self) -> AsyncIterator[bytes]:
        """Yield MJPG frames as they arrive."""
        event = asyncio.Event()
        self._subscribers.append(event)
        try:
            while True:
                await event.wait()
                event.clear()
                if self._latest_frame:
                    yield self._latest_frame
        finally:
            self._subscribers.remove(event)

    def latest_snapshot(self) -> bytes:
        return self._latest_frame


class CameraManager:
    """Manages multiple camera streams by name."""

    def __init__(self, bridge: RosBridge) -> None:
        self._bridge = bridge
        self._streams: dict[str, CameraStream] = {}

    def get(self, name: str, topic: str) -> CameraStream:
        if name not in self._streams:
            self._streams[name] = CameraStream(self._bridge, topic)
        return self._streams[name]

    def subscribe_all(self, topics: dict[str, str]) -> None:
        for name, topic in topics.items():
            stream = self.get(name, topic)
            stream.subscribe()
