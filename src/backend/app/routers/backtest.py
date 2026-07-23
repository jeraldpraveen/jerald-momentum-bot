"""Backtest routes — research only, on their own /api/backtest prefix.

Nothing here can reach the live cycle, ledger or journal; the backtest owns a
separate data tree and shares only the read-only strategy core.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from ..schemas.backtest import (
    AltAssetOption,
    BacktestResult,
    BacktestRunRequest,
    BuildUniverseRequest,
    IndexCoverage,
    RunRecord,
    UniverseSummary,
)
from ..services import backtest_service

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.get("/alt-assets", response_model=list[AltAssetOption])
def list_alt_assets() -> list[AltAssetOption]:
    """Assets that RED-week capital can be parked in instead of idle cash."""
    from .. import config as app_config
    return [AltAssetOption(**vars(o)) for o in app_config.ALT_ASSET_OPTIONS]


@router.get("/index-coverage", response_model=list[IndexCoverage])
def index_coverage() -> list[IndexCoverage]:
    """How far back each regime index can gate a run — shown before starting one."""
    return backtest_service.index_coverage()


# --- universes --------------------------------------------------------------

@router.get("/universes", response_model=list[UniverseSummary])
def list_universes() -> list[UniverseSummary]:
    return backtest_service.list_universes()


@router.post("/universes", response_model=UniverseSummary,
             status_code=status.HTTP_201_CREATED)
def build_universe(body: BuildUniverseRequest) -> UniverseSummary:
    """Snapshot today's constituents of one or more NSE indices into a file."""
    return backtest_service.build_universe(body)


@router.get("/universes/{universe_id}", response_model=UniverseSummary)
def get_universe(universe_id: str) -> UniverseSummary:
    try:
        return backtest_service.summarize_universe(universe_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/universes/{universe_id}")
def delete_universe(universe_id: str) -> dict:
    if not backtest_service.delete_universe(universe_id):
        raise HTTPException(status_code=404, detail=f"No universe {universe_id!r}.")
    return {"deleted": universe_id}


# --- runs -------------------------------------------------------------------

@router.post("/runs", response_model=RunRecord, status_code=status.HTTP_202_ACCEPTED)
def start_run(body: BacktestRunRequest) -> RunRecord:
    """Queue a run on a background thread; poll the record for progress."""
    return backtest_service.start_run(body)


@router.get("/runs", response_model=list[RunRecord])
def list_runs(limit: int = 50) -> list[RunRecord]:
    return backtest_service.list_runs(limit)


@router.get("/runs/{run_id}", response_model=RunRecord)
def get_run(run_id: str) -> RunRecord:
    rec = backtest_service.get_run(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"No run {run_id!r}.")
    return rec


@router.get("/runs/{run_id}/result", response_model=BacktestResult)
def get_result(run_id: str) -> BacktestResult:
    res = backtest_service.get_result(run_id)
    if res is None:
        raise HTTPException(
            status_code=404,
            detail=f"No result for {run_id!r} — it may still be running, or it failed.")
    return res


@router.post("/runs/{run_id}/cancel", response_model=dict)
def cancel_run(run_id: str) -> dict:
    return {"cancelling": backtest_service.cancel_run(run_id)}


@router.delete("/runs/{run_id}")
def delete_run(run_id: str) -> dict:
    if not backtest_service.delete_run(run_id):
        raise HTTPException(status_code=404, detail=f"No run {run_id!r}.")
    return {"deleted": run_id}
