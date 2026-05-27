"""ASGI middleware that logs API request/response body in one line."""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("api.access")

_BODY_MAX = 2000
_SKIP_PREFIXES = ("/api/robot/docs", "/api/robot/redoc", "/api/robot/openapi.json")


def _short(b: bytes) -> str:
    if not b:
        return "-"
    s = b.decode("utf-8", errors="replace")
    if len(s) > _BODY_MAX:
        s = s[:_BODY_MAX] + f"...<+{len(s) - _BODY_MAX}B>"
    return s.replace("\n", " ")


class APIAccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)

        req_body = await request.body()

        async def receive():
            return {"type": "http.request", "body": req_body, "more_body": False}

        request._receive = receive

        start = time.perf_counter()
        response = await call_next(request)
        resp_chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            resp_chunks.append(chunk)
        resp_body = b"".join(resp_chunks)
        elapsed_ms = (time.perf_counter() - start) * 1000

        client = request.client.host if request.client else "-"
        logger.info(
            "%s %s %s -> %d  %.1fms  req=%s  resp=%s",
            client,
            request.method,
            path,
            response.status_code,
            elapsed_ms,
            _short(req_body),
            _short(resp_body),
        )

        return Response(
            content=resp_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
