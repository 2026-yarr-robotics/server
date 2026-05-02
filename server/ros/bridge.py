"""Singleton rosbridge WebSocket client using roslibpy."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import roslibpy

from ..config import RosBridgeConfig

logger = logging.getLogger(__name__)


class RosBridge:
    """Manages a single rosbridge connection shared across the application."""

    _instance: RosBridge | None = None

    def __init__(self, config: RosBridgeConfig) -> None:
        self._config = config
        self._ros: roslibpy.Ros | None = None
        self._subscribers: dict[str, roslibpy.Topic] = {}

    @classmethod
    def get(cls, config: RosBridgeConfig | None = None) -> RosBridge:
        if cls._instance is None:
            if config is None:
                config = RosBridgeConfig()
            cls._instance = cls(config)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    @property
    def ros(self) -> roslibpy.Ros:
        if self._ros is None:
            raise RuntimeError("RosBridge not connected. Call connect() first.")
        return self._ros

    @property
    def connected(self) -> bool:
        return self._ros is not None and self._ros.is_connected

    def connect(self) -> None:
        if self._ros is not None and self._ros.is_connected:
            return

        self._ros = roslibpy.Ros(
            host=self._config.host,
            port=self._config.port,
        )
        self._ros.on("connected", lambda: logger.info("rosbridge connected"))
        self._ros.on("disconnected", lambda: logger.warning("rosbridge disconnected"))
        self._ros.on("error", lambda e: logger.error("rosbridge error: %s", e))
        self._ros.run()
        logger.info(
            "RosBridge connecting to %s:%d",
            self._config.host,
            self._config.port,
        )

    def disconnect(self) -> None:
        if self._ros is not None:
            for topic in self._subscribers.values():
                topic.unsubscribe()
            self._subscribers.clear()
            self._ros.close()
            self._ros = None

    def subscribe(
        self,
        topic_name: str,
        msg_type: str,
        callback: Callable[[dict[str, Any]], None],
        throttle_rate: int = 0,
        queue_size: int = 1,
    ) -> roslibpy.Topic:
        if topic_name in self._subscribers:
            sub = self._subscribers[topic_name]
            sub.subscribe(callback)
            return sub

        sub = roslibpy.Topic(
            self.ros,
            topic_name,
            msg_type,
            throttle_rate=throttle_rate,
            queue_size=queue_size,
        )
        sub.subscribe(callback)
        self._subscribers[topic_name] = sub
        return sub

    def unsubscribe(self, topic_name: str) -> None:
        sub = self._subscribers.pop(topic_name, None)
        if sub is not None:
            sub.unsubscribe()

    def call_service(
        self,
        service_name: str,
        service_type: str,
        args: dict[str, Any] | None = None,
    ) -> Any:
        service = roslibpy.Service(self.ros, service_name, service_type)
        return service.call(args or {})


async def connect_bridge(config: RosBridgeConfig) -> RosBridge:
    bridge = RosBridge.get(config)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, bridge.connect)

    elapsed = 0.0
    interval = 0.25
    log_every = 5.0
    while not bridge.connected:
        await asyncio.sleep(interval)
        elapsed += interval
        if elapsed % log_every < interval:
            logger.info(
                "Waiting for rosbridge at %s:%d (%.0fs)...",
                config.host,
                config.port,
                elapsed,
            )

    logger.info("Connected to rosbridge at %s:%d", config.host, config.port)
    return bridge


async def disconnect_bridge() -> None:
    bridge = RosBridge.get()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, bridge.disconnect)
