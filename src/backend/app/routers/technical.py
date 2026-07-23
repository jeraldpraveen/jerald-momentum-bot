"""Step-5 routes — the technical veto (Part D, checks 3/4/5)."""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas.cycle import Cycle
from ..services import cycle_service, technical_service

router = APIRouter(prefix="/api/cycles/current/steps/part-d-technical", tags=["part-d"])


@router.post("/run", response_model=Cycle)
def run_technical() -> Cycle:
    """50-DMA trend, 20-DMA extension and RSI(14) on Step-4 survivors. Fully automatic."""
    return technical_service.run()


@router.post("/advance", response_model=Cycle)
def advance_technical() -> Cycle:
    return cycle_service.advance_part_d_technical()
