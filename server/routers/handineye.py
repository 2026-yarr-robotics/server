"""REST API endpoints for hand-in-eye calibration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domains.handineye import HandInEyeDomain
from ..schemas import CalibrationResponse, CalibrationUpdateRequest

router = APIRouter(prefix="/api/handineye", tags=["handineye"])

handineye_domain: HandInEyeDomain | None = None


def set_handineye_domain(domain: HandInEyeDomain) -> None:
    global handineye_domain
    handineye_domain = domain


def _get_domain() -> HandInEyeDomain:
    if handineye_domain is None:
        raise HTTPException(status_code=503, detail="Hand-in-eye domain not initialized")
    return handineye_domain


@router.get("/calibration", response_model=CalibrationResponse)
async def get_calibration() -> dict:
    return _get_domain().get_calibration()


@router.put("/calibration", response_model=CalibrationResponse)
async def update_calibration(body: CalibrationUpdateRequest) -> dict:
    return _get_domain().update_calibration(body.matrix)
