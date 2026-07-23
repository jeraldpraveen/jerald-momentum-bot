"""Step-7 routes — journal finalize (Part E §2.11) + reading past entries."""

from __future__ import annotations

from fastapi import APIRouter

from ..repositories import journal_repository
from ..schemas.cycle import Cycle
from ..schemas.journal import JournalEntry, JournalRunRequest
from ..services import cycle_service, journal_service

router = APIRouter(prefix="/api", tags=["part-e"])


@router.get("/journal", response_model=list[JournalEntry])
def list_journal(limit: int = 50) -> list[JournalEntry]:
    """Past weekly entries, most recent first. Survives cycle deletion."""
    return journal_repository.read_entries(limit)


@router.post("/cycles/current/steps/part-e/run", response_model=Cycle)
def run_journal(body: JournalRunRequest | None = None) -> Cycle:
    """Compile this week's journal row (preview)."""
    return journal_service.run(body or JournalRunRequest())


@router.post("/cycles/current/steps/part-e/advance", response_model=Cycle)
def advance_journal() -> Cycle:
    """Append the row to the durable journal, then move to the Monday plan."""
    return cycle_service.advance_part_e_journal()
