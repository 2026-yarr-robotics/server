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

import cv2
import cbor2
import numpy as np
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
        # ROS 노드(vision 팀 공유)는 1280x720x30 그대로 두고, 외부 cloudflared
        # 단의 throughput 한계(~5 Mbps)에 맞춰 이 서버 단에서 한 번 더
        # 다운스케일+저화질 JPEG 으로 re-encode 한 뒤 브라우저로 push 한다.
        # throttle_rate_ms=100 → rosbridge 측에서 미리 10 FPS 로 제한해
        # 들여 오는 frame 자체를 줄여 CPU 부담도 감소.
        throttle_rate_ms: int = 100,
        # Downscale + lower JPEG quality so the steady-state bitrate stays under
        # the ~5 Mbps cloudflared tunnel ceiling. Above the ceiling, frames pile
        # up in the TCP/cloudflared send buffer (which the app-level
        # drop-to-latest in frames() cannot evict) and the view runs visibly
        # behind real time. 640w @ q35 roughly halves the payload vs 800w @ q50.
        target_width: int = 640,
        jpeg_quality: int = 35,
    ) -> None:
        self._config = config
        self._topic = topic
        self._throttle_rate_ms = throttle_rate_ms
        self._target_width = target_width
        self._jpeg_quality = jpeg_quality
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
                # ping_interval=None: disable the websockets-legacy client
                # keepalive. Under send-buffer back-pressure its keepalive_ping
                # task hits an internal drain race (AssertionError in
                # _drain_helper -> "keepalive ping failed") and tears the
                # connection down, causing periodic ~1s camera dropouts. We are
                # a read-only subscriber, so liveness is detected by a recv
                # inactivity timeout instead (frames arrive at ~10 Hz).
                async with websockets.connect(
                    uri,
                    max_size=32 * 1024 * 1024,
                    ping_interval=None,
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
                    while not self._stopping:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                        except asyncio.TimeoutError:
                            logger.warning(
                                "camera %s: no frame for 10s; reconnecting",
                                self._topic,
                            )
                            break
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
        # cbor-raw: ROS 메시지를 CDR wire-format 그대로 'bytes' 필드에 담아 보낸다
        # (header.stamp만 secs/nsecs로 따로 발라냄). CompressedImage의 경우
        # frame_id/format 문자열 헤더 뒤로 JPEG payload가 이어지므로
        # JPEG SOI(FF D8 FF)부터 끝까지 잘라내면 바로 사용 가능.
        payload = inner.get("bytes")
        if not isinstance(payload, (bytes, bytearray)):
            return
        payload = bytes(payload)
        jpeg_start = payload.find(JPEG_PREFIX)
        if jpeg_start < 0:
            logger.debug(
                "no JPEG SOI in %s payload (len=%d)", self._topic, len(payload)
            )
            return
        src_jpeg = payload[jpeg_start:]
        # 외부 throughput 부담을 줄이기 위해 한 번 더 작게 re-encode.
        # 원본보다 폭이 작거나 같으면 decode 비용 없이 원본을 그대로 쓴다.
        try:
            arr = np.frombuffer(src_jpeg, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                self._latest_frame = src_jpeg
            else:
                h, w = img.shape[:2]
                if w > self._target_width:
                    scale = self._target_width / float(w)
                    new_size = (self._target_width, int(round(h * scale)))
                    img = cv2.resize(img, new_size, interpolation=cv2.INTER_AREA)
                ok, encoded = cv2.imencode(
                    ".jpg", img,
                    [int(cv2.IMWRITE_JPEG_QUALITY), int(self._jpeg_quality)],
                )
                self._latest_frame = encoded.tobytes() if ok else src_jpeg
        except Exception:
            logger.exception("re-encode failed for %s", self._topic)
            self._latest_frame = src_jpeg
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
