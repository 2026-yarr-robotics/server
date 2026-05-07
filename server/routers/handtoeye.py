"""REST API endpoints for hand-to-eye calibration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domains.handtoeye import HandToEyeDomain
from ..schemas import CalibrationResponse, CalibrationUpdateRequest

router = APIRouter(prefix="/api/handtoeye", tags=["handtoeye"])

handtoeye_domain: HandToEyeDomain | None = None


def set_handtoeye_domain(domain: HandToEyeDomain) -> None:
    global handtoeye_domain
    handtoeye_domain = domain


def _get_domain() -> HandToEyeDomain:
    if handtoeye_domain is None:
        raise HTTPException(status_code=503, detail="Hand-to-eye domain not initialized")
    return handtoeye_domain


@router.get("/calibration", response_model=CalibrationResponse)
async def get_calibration() -> dict:
    return _get_domain().get_calibration()


@router.put("/calibration", response_model=CalibrationResponse)
async def update_calibration(body: CalibrationUpdateRequest) -> dict:
    return _get_domain().update_calibration(body.matrix)
