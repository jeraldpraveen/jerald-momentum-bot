"""Part C routes — run the portfolio audit, then advance (commits the exits)."""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas.cycle import Cycle
from ..services import cycle_service, exit_flags_service

router = APIRouter(prefix="/api/cycles/current/steps/part-c", tags=["part-c"])


@router.post("/run", response_model=Cycle)
def run_part_c() -> Cycle:
    """Audit ACTIVE holdings against the Jerald Score ranking. Preview only."""
    return exit_flags_service.run()


@router.post("/advance", response_model=Cycle)
def advance_part_c() -> Cycle:
    """Commit: mark flagged holdings PENDING_EXIT, move to the veto steps."""
    return cycle_service.advance_part_c()
