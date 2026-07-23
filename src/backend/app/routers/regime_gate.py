"""Part A routes — regime options, run the gate, then advance after review."""

from __future__ import annotations

from fastapi import APIRouter

from .. import config
from ..schemas.cycle import Cycle
from ..schemas.regime_gate import (
    FilterOptionOut,
    IndexOptionOut,
    RegimeGateRunRequest,
    RegimeOptions,
)
from ..services import cycle_service, regime_gate_service

router = APIRouter(prefix="/api", tags=["part-a"])


@router.get("/regime-options", response_model=RegimeOptions)
def regime_options() -> RegimeOptions:
    """The selectable indices + filters for the Regime Gate dropdowns."""
    return RegimeOptions(
        indices=[
            IndexOptionOut(id=o.id, label=o.label, ticker=o.ticker, kind=o.kind, note=o.note)
            for o in config.INDEX_OPTIONS
        ],
        filters=[
            FilterOptionOut(id=o.id, label=o.label, indicator=o.indicator)
            for o in config.FILTER_OPTIONS
        ],
        default_index_id=config.DEFAULT_INDEX_ID,
        default_filter_id=config.DEFAULT_FILTER_ID,
    )


@router.post("/cycles/current/steps/part-a/run", response_model=Cycle)
def run_part_a(body: RegimeGateRunRequest | None = None) -> Cycle:
    """Fetch the chosen index -> apply the chosen filter -> RED/GREEN -> persist.

    Idempotent. A data/compute failure comes back as the Part A step in status
    `failed` with an `error` message (HTTP 200), not a 500.
    """
    body = body or RegimeGateRunRequest()
    return regime_gate_service.run(index_id=body.index_id, filter_id=body.filter_id)


@router.post("/cycles/current/steps/part-a/advance", response_model=Cycle)
def advance_part_a() -> Cycle:
    """User confirms review -> move current_step per the RED/GREEN branch."""
    return cycle_service.advance_part_a()
