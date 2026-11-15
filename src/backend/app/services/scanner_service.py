"""Part B — Stock Scoring service (the Jerald Score; replaces the Sigma Scanner Excel upload).

Resolve the chosen universe → fetch/cache per-stock prices → compute the Jerald
momentum score as-of the chosen date → apply eligibility (liquidity, listing age)
and the screener filters (sector, price range) → return the full ranking with
per-name eligibility flags. Never mutates the ledger; only computes.
"""

from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd

from .. import config
from ..domain import scoring_formula
from ..domain.strategy import build_candidates as core_build_candidates
from ..domain.strategy.scoring import median_traded_value_cr
from ..integrations import constituents, stock_prices
from ..repositories import cycles_repository as repo  # noqa: F401  (parity w/ other services)
from ..repositories import ledger_repository as ledger
from ..schemas.common import StepStatus
from ..schemas.cycle import Cycle, StepId
from ..schemas.scanner import (
    PartBRunRequest,
    ScannerCandidate,
    ScannerFilters,
    ScannerOutput,
)
from . import cycle_service

log = logging.getLogger(__name__)


def _resolve_universe(universe: config.UniverseOption) -> list[tuple[str, str, str]]:
    """Return [(ticker.NS, sector, company)] for a single universe."""
    if universe.source == "holdings":
        df = ledger.load_ledger()
        return [(f"{s}.NS", str(sec), "") for s, sec in zip(df["symbol"], df["sector"])]
    rows = constituents.fetch_constituents(universe.nse_key)
    return [(f"{r.symbol}.NS", str(r.sector), str(r.company)) for r in rows.itertuples()]


def _resolve_universes(universe_ids: list[str]) -> list[tuple[str, str, str]]:
    """Union of constituents across the selected universes, deduped by ticker."""
    seen: dict[str, tuple[str, str, str]] = {}
    for uid in universe_ids:
        for ticker, sector, company in _resolve_universe(config.get_universe_option(uid)):
            seen.setdefault(ticker, (ticker, sector, company))
    return list(seen.values())


def list_sectors(universe_ids: list[str]) -> list[str]:
    members = _resolve_universes(universe_ids or list(config.DEFAULT_UNIVERSE_IDS))
    return sorted({sec for _, sec, _ in members if sec and sec != "nan"})


def _build_candidates(
    universe_ids: list[str], compiled: scoring_formula.CompiledFormula, filters: ScannerFilters,
) -> tuple[list[ScannerCandidate], dt.date]:
    """Resolve the universe and fetch prices (I/O), then delegate the ranking.

    The ranking itself lives in `domain.strategy.scoring` so the backtest scores
    historical weeks with exactly this code.
    """
    members = _resolve_universes(universe_ids)
    prices = stock_prices.get_prices([t for t, _, _ in members], as_of=filters.as_of)
    as_of = filters.as_of or dt.date.today()
    # The core judges price staleness against the as-of date, so it always gets
    # one — a blank date means "today", not "do not check".
    dated = filters.model_copy(update={"as_of": as_of})
    return core_build_candidates(members, prices, compiled, dated), as_of


def run(body: PartBRunRequest, now_ist: dt.datetime | None = None) -> Cycle:
    """Execute Part B against the current cycle. Idempotent; failures land on the step."""
    now_ist = now_ist or dt.datetime.now(config.IST)
    cycle = cycle_service.get_current_cycle()
    cycle_service.require_open(cycle)
    if cycle.current_step != StepId.PART_B_SCANNER.value:
        from .errors import StepNotReady
        raise StepNotReady("Part B is available only after Part A advances on GREEN.")

    # Spec v2.3: the live score is frozen. A different formula is refused here,
    # before anything is computed, rather than quietly ranking the week on an
    # untested score.
    if not scoring_formula.is_frozen(body.formula):
        from .errors import FormulaFrozen
        raise FormulaFrozen(
            "The live scoring formula is frozen (Spec v2.3) and cannot be changed in "
            "the Friday ritual. Try alternatives in the Backtest tab; adopting one is "
            "a Rulebook amendment.",
            frozen_formula=scoring_formula.FROZEN_FORMULA,
        )

    universe_ids = body.universe_ids or list(config.DEFAULT_UNIVERSE_IDS)
    formula = scoring_formula.FROZEN_FORMULA

    step = cycle.steps.part_b_scanner
    step.status = StepStatus.IN_PROGRESS
    step.started_at = now_ist
    step.error = None
    step.input = {"universe_ids": universe_ids, "formula": formula}

    try:
        options = [config.get_universe_option(uid) for uid in universe_ids]
        compiled = scoring_formula.compile_formula(formula)   # FormulaError -> step failed
        candidates, as_of = _build_candidates(universe_ids, compiled, body.filters)
        step.output = ScannerOutput(
            universe_ids=universe_ids,
            universe_label=" + ".join(o.label for o in options),
            as_of=as_of.isoformat(),
            formula=formula,
            filters=body.filters,
            total=len(candidates),
            eligible_count=sum(1 for c in candidates if c.eligible),
            candidates=candidates,
        )
        step.status = StepStatus.DONE
    except Exception as exc:  # noqa: BLE001
        log.exception("Part B failed for cycle %s", cycle.cycle_id)
        step.status = StepStatus.FAILED
        step.output = None
        step.error = f"{type(exc).__name__}: {exc}"
    finally:
        step.completed_at = now_ist

    return cycle_service.touch_and_save(cycle, now_ist)
