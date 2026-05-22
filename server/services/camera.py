"""Camera streaming service.

JPEG 프레임을 rosbridge로부터 받아 브라우저 WebSocket으로 push.

rosbridge protocol에 **직접 WebSocket 클라이언트**로 붙어
``compression: cbor-raw`` 모드로 구독한다. roslibpy가 쓰는 기본
JSON+base64 모드는 한 프레임당 33% 크기 팽창과 JSON 직렬화
비용이 누적되어 30 FPS CompressedImage 스트림에서 백로그를 만든다.
CBOR raw는 바이너리를 그대로 전달하므로 그 두 비용이 모두 사라진다.

다른 (이미지가 아닌) 토픽은 여전히 ``ros.bridge.RosBridge`` 싱글톤이
roslibpy로 처리한다 — 이 모듈은 카메라 토픽만 우회 처리한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import cbor2
import websockets

from ..config import RosBridgeConfig

logger = logging.getLogger(__name__)

JPEG_PREFIX = b"\xff\xd8\xff"


class CameraStream:
    """rosbridge에 직접 붙어 cbor-raw로 CompressedImage를 받는 스트림."""

    def __init__(
        self,
        config: RosBridgeConfig,
        topic: str,
        *,
        throttle_rate_ms: int = 33,
    ) -> None:
        self._config = config
        self._topic = topic
        self._throttle_rate_ms = throttle_rate_ms
        self._latest_frame: bytes = b""
        self._subscribers: list[asyncio.Event] = []
        self._task: asyncio.Task | None = None
        self._stopping = False

    def subscribe(self) -> None:
        """백그라운드 태스크로 rosbridge 연결 + 구독 시작."""
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._run(), name=f"camera-cbor:{self._topic}"
        )
        logger.info("Camera subscribed to %s (cbor-raw)", self._topic)

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        uri = f"ws://{self._config.host}:{self._config.port}/"
        backoff = 1.0
        while not self._stopping:
            try:
                # CompressedImage 한 프레임이 수십 KB이므로 max_size를 넉넉히.
                async with websockets.connect(
                    uri,
                    max_size=32 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    backoff = 1.0
                    await ws.send(json.dumps({
                        "op": "subscribe",
                        "topic": self._topic,
                        "type": "sensor_msgs/msg/CompressedImage",
                        "throttle_rate": self._throttle_rate_ms,
                        "queue_length": 1,
                        "compression": "cbor-raw",
                    }))
                    logger.info(
                        "rosbridge subscribe sent: topic=%s compression=cbor-raw",
                        self._topic,
                    )
                    async for raw in ws:
                        self._handle_message(raw)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning(
                    "camera stream %s disconnected; reconnecting in %.1fs",
                    self._topic,
                    backoff,
                    exc_info=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)

    def _handle_message(self, raw: bytes | str) -> None:
        # cbor-raw 모드에서 rosbridge는 바이너리 CBOR 프레임을 보낸다.
        # 다른 협상 메시지는 텍스트(JSON)일 수 있으니 텍스트는 무시.
        if not isinstance(raw, (bytes, bytearray)):
            return
        try:
            msg = cbor2.loads(raw)
        except Exception:
            logger.debug("cbor decode failed for %s", self._topic, exc_info=True)
            return
        if not isinstance(msg, dict):
            return
        inner = msg.get("msg")
        if not isinstance(inner, dict):
            return
        data = inner.get("data")
        # cbor-raw: byte string으로 도착 (base64 아님)
        if not isinstance(data, (bytes, bytearray)):
            return
        jpeg = bytes(data)
        if not jpeg.startswith(JPEG_PREFIX):
            # PNG 등 다른 포맷이면 트랜스코딩 (드물지만 안전망)
            try:
                import cv2
                import numpy as np

                nparr = np.frombuffer(jpeg, dtype=np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if frame is None:
                    return
                _, encoded = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80]
                )
                jpeg = encoded.tobytes()
            except Exception:
                return
        self._latest_frame = jpeg
        for event in self._subscribers:
            event.set()

    async def frames(self) -> AsyncIterator[bytes]:
        """Yield JPEG frames as they arrive."""
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

    def __init__(self, config: RosBridgeConfig) -> None:
        self._config = config
        self._streams: dict[str, CameraStream] = {}

    def get(self, name: str, topic: str) -> CameraStream:
        if name not in self._streams:
            self._streams[name] = CameraStream(self._config, topic)
        return self._streams[name]

    def subscribe_all(self, topics: dict[str, str]) -> None:
        for name, topic in topics.items():
            stream = self.get(name, topic)
            stream.subscribe()

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(s.stop() for s in self._streams.values()),
            return_exceptions=True,
        )
