"""REST API endpoints for hand-to-eye calibration."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..domains.handtoeye import HandToEyeDomain

router = APIRouter(prefix="/api/handtoeye", tags=["handtoeye"])

handtoeye_domain: HandToEyeDomain | None = None


def set_handtoeye_domain(domain: HandToEyeDomain) -> None:
    global handtoeye_domain
    handtoeye_domain = domain


def _get_domain() -> HandToEyeDomain:
    if handtoeye_domain is None:
        raise HTTPException(status_code=503, detail="Hand-to-eye domain not initialized")
    return handtoeye_domain


@router.get("/calibration")
async def get_calibration() -> dict[str, Any]:
    return _get_domain().get_calibration()


@router.put("/calibration")
async def update_calibration(body: dict[str, Any]) -> dict[str, Any]:
    matrix_data = body.get("matrix")
    if not matrix_data:
        raise HTTPException(status_code=400, detail="Missing 'matrix' field")
    return _get_domain().update_calibration(matrix_data)
