"""REST API endpoints for hand-in-eye calibration."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..domains.handineye import HandInEyeDomain

router = APIRouter(prefix="/api/handineye", tags=["handineye"])

handineye_domain: HandInEyeDomain | None = None


def set_handineye_domain(domain: HandInEyeDomain) -> None:
    global handineye_domain
    handineye_domain = domain


def _get_domain() -> HandInEyeDomain:
    if handineye_domain is None:
        raise HTTPException(status_code=503, detail="Hand-in-eye domain not initialized")
    return handineye_domain


@router.get("/calibration")
async def get_calibration() -> dict[str, Any]:
    return _get_domain().get_calibration()


@router.put("/calibration")
async def update_calibration(body: dict[str, Any]) -> dict[str, Any]:
    matrix_data = body.get("matrix")
    if not matrix_data:
        raise HTTPException(status_code=400, detail="Missing 'matrix' field")
    return _get_domain().update_calibration(matrix_data)
