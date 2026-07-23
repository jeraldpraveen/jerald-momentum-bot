"""Step-4 routes — the fundamental veto (Part D, checks 1/2/6/7)."""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas.cycle import Cycle
from ..schemas.fundamental import PartDFundamentalRunRequest
from ..services import cycle_service, fundamental_service

router = APIRouter(prefix="/api/cycles/current/steps/part-d-fundamental", tags=["part-d"])


@router.post("/run", response_model=Cycle)
def run_fundamental(body: PartDFundamentalRunRequest | None = None) -> Cycle:
    """Evaluate ranks 1-15 (excluding holdings). Send edited assessments to re-evaluate."""
    return fundamental_service.run(body or PartDFundamentalRunRequest())


@router.post("/advance", response_model=Cycle)
def advance_fundamental() -> Cycle:
    return cycle_service.advance_part_d_fundamental()
