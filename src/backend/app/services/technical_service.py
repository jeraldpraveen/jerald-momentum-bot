"""Part D / Step-5 — Technical veto service (Spec v2.2 checks 3/4/5).

Evaluates only the candidates that SURVIVED Step-4 (the veto is a sequential
eliminate-only ladder — a name already struck on fundamentals isn't re-examined).

All three checks are computed from cached daily prices, so every verdict is
cited. A stock without enough history is flagged "unverified" and passes,
consistent with the Rulebook's data-safeguard.
"""

from __future__ import annotations

import datetime as dt
import logging

from .. import config
from ..domain.strategy import RankedName, evaluate_technical
from ..integrations import stock_prices
from ..schemas.common import StepStatus
from ..schemas.cycle import Cycle, StepId
from ..schemas.technical import TechnicalOutput
from . import cycle_service
from .errors import StepNotReady

log = logging.getLogger(__name__)


def _evaluate(cycle: Cycle) -> TechnicalOutput:
    fund = cycle.steps.part_d_fundamental.output
    if fund is None:
        raise StepNotReady("Run the fundamental veto (Step 4) before the technical checks.")
    scan = cycle.steps.part_b_scanner.output
    as_of = scan.as_of if scan else dt.date.today().isoformat()

    survivors = [c for c in fund.candidates if not c.struck]
    tickers = [f"{c.symbol}.NS" for c in survivors]
    as_of_date = dt.date.fromisoformat(as_of) if scan else None
    prices = stock_prices.get_prices(tickers, as_of=as_of_date) if tickers else {}

    rows = evaluate_technical(
        [RankedName(symbol=c.symbol, rank=c.rank, sector=c.sector) for c in survivors],
        prices,
    )

    struck = sum(1 for r in rows if r.struck)
    passers = [r.symbol for r in rows if not r.struck]
    actions = [
        f"Evaluated {len(rows)} name(s) surviving Step 4: {struck} struck, {len(passers)} surviving.",
        f"Checks: price < {config.DMA_TREND_PERIOD}-DMA, "
        f"> {config.MAX_EXTENSION_PCT}% above {config.DMA_EXTENSION_PERIOD}-DMA, "
        f"RSI({config.RSI_PERIOD}) > {config.MAX_RSI}.",
    ]
    return TechnicalOutput(
        as_of=as_of, candidates=rows, struck_count=struck,
        passed_count=len(passers), survivors=passers, actions=actions,
    )


def run(now_ist: dt.datetime | None = None) -> Cycle:
    """Evaluate the technical veto. Fully automatic — no manual input."""
    now_ist = now_ist or dt.datetime.now(config.IST)
    cycle = cycle_service.get_current_cycle()
    cycle_service.require_open(cycle)
    if cycle.current_step != StepId.PART_D_TECHNICAL.value:
        raise StepNotReady("Step 5 is available only after the fundamental veto advances.")

    step = cycle.steps.part_d_technical
    step.status = StepStatus.IN_PROGRESS
    step.started_at = now_ist
    step.error = None

    try:
        step.output = _evaluate(cycle)
        step.status = StepStatus.DONE
    except Exception as exc:  # noqa: BLE001
        log.exception("Step 5 failed for cycle %s", cycle.cycle_id)
        step.status = StepStatus.FAILED
        step.output = None
        step.error = f"{type(exc).__name__}: {exc}"
    finally:
        step.completed_at = now_ist

    return cycle_service.touch_and_save(cycle, now_ist)
