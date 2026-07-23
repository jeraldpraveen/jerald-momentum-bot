"""Step-8 routes — the Monday execution plan (Phase 3 §3.1-3.4)."""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas.cycle import Cycle
from ..schemas.phase3 import Phase3RunRequest
from ..services import cycle_service, phase3_service

router = APIRouter(prefix="/api/cycles/current/steps/phase3", tags=["phase-3"])


@router.post("/run", response_model=Cycle)
def run_phase3(body: Phase3RunRequest | None = None) -> Cycle:
    """Build the by-hand checklist for the open. Places no orders."""
    return phase3_service.run(body or Phase3RunRequest())


@router.post("/advance", response_model=Cycle)
def complete_cycle() -> Cycle:
    """Acknowledge the plan and mark the weekly ritual complete."""
    return cycle_service.complete_cycle()
