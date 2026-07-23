"""Cycle routes — thin: parse request -> call service -> return."""

from __future__ import annotations

from fastapi import APIRouter, status

from ..schemas.cycle import Cycle, CycleSummary
from ..schemas.requests import CreateCycleRequest
from ..services import cycle_service

router = APIRouter(prefix="/api/cycles", tags=["cycles"])


@router.post("", response_model=Cycle, status_code=status.HTTP_201_CREATED)
def create_cycle(body: CreateCycleRequest | None = None) -> Cycle:
    body = body or CreateCycleRequest()
    return cycle_service.create_cycle(week_end=body.week_end)


@router.get("", response_model=list[CycleSummary])
def list_cycles() -> list[CycleSummary]:
    from ..repositories import cycles_repository as repo
    return repo.list_cycles()


@router.get("/current", response_model=Cycle)
def get_current_cycle() -> Cycle:
    return cycle_service.get_current_cycle()


@router.post("/current/close", response_model=Cycle)
def close_current_cycle() -> Cycle:
    """Close the current cycle (finish or abandon) so a new one can start."""
    return cycle_service.close_current_cycle()


@router.delete("/current", status_code=status.HTTP_200_OK)
def delete_current_cycle() -> dict:
    """Permanently delete the current cycle file and its journal rows."""
    cycle_id, journal_removed = cycle_service.delete_current_cycle()
    return {"deleted": cycle_id, "journal_entries_removed": journal_removed}


@router.post("/current/steps/{step_id}/reopen", response_model=Cycle)
def reopen_step(step_id: str) -> Cycle:
    """Go back to an earlier step to edit and re-run it; resets downstream steps."""
    return cycle_service.reopen_step(step_id)


@router.get("/{cycle_id}", response_model=Cycle)
def get_cycle(cycle_id: str) -> Cycle:
    return cycle_service.get_cycle(cycle_id)
