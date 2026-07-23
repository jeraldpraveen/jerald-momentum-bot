"""Step-6 routes — risk caps & slot filling (Part D §2.8/§2.10)."""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas.cycle import Cycle
from ..services import cycle_service, risk_caps_service

router = APIRouter(prefix="/api/cycles/current/steps/part-d-risk-caps", tags=["part-d"])


@router.post("/run", response_model=Cycle)
def run_risk_caps() -> Cycle:
    """Fill open slots from Step-5 survivors under the sector/microcap caps."""
    return risk_caps_service.run()


@router.post("/advance", response_model=Cycle)
def advance_risk_caps() -> Cycle:
    return cycle_service.advance_part_d_risk_caps()
