"""Part D / Step-6 — Risk caps & slot filling (Spec v2.2 §2.8 + §2.10).

Walks the Step-5 survivors in strict rank order, adding each to the buy queue
unless it would breach a concentration cap. Caps are evaluated against the
resulting portfolio (kept holdings + buys so far), not just the new names.

Unfilled slots deliberately stay in cash — the loop never reaches past rank 15
to force a fill, and never re-ranks or substitutes.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import Counter

from .. import config
from ..integrations import constituents
from ..repositories import ledger_repository as ledger
from ..schemas.common import StepStatus
from ..schemas.cycle import Cycle, StepId
from ..domain.strategy import Position, RankedName, apply_caps
from ..schemas.risk_caps import RiskCapsOutput
from . import cycle_service
from .errors import StepNotReady

log = logging.getLogger(__name__)

def _microcap_symbols() -> set[str]:
    """Symbols in the Microcap 250 sleeve (for the max-4 cap)."""
    try:
        opt = config.get_universe_option("microcap250")
        df = constituents.fetch_constituents(opt.nse_key)
        return {str(s).strip().upper() for s in df["symbol"]}
    except Exception as exc:  # noqa: BLE001 — cap simply can't bind if unavailable
        log.info("Microcap list unavailable (%s); microcap cap not enforced",
                 type(exc).__name__)
        return set()


def _evaluate(cycle: Cycle) -> RiskCapsOutput:
    tech = cycle.steps.part_d_technical.output
    if tech is None:
        raise StepNotReady("Run the technical veto (Step 5) before the risk caps.")
    scan = cycle.steps.part_b_scanner.output
    as_of = scan.as_of if scan else dt.date.today().isoformat()

    # Holdings being kept (Part C already marked exits as PENDING_EXIT).
    df = ledger.load_ledger()
    active = df[df["status"] == "ACTIVE"]
    held = [Position(symbol=str(sym), sector=str(sec), sleeve=str(sl))
            for sym, sec, sl in zip(active["symbol"], active["sector"], active["sleeve"])]

    res = apply_caps(
        [RankedName(symbol=c.symbol, rank=c.rank, sector=c.sector)
         for c in tech.candidates if not c.struck],
        held,
        _microcap_symbols(),
    )
    kept, buys, skipped = res.kept, res.buys, res.skipped
    open_slots, sector_counts, microcap_count = res.open_slots, res.sector_counts, res.microcap_count

    unfilled = res.unfilled_slots
    final_size = res.final_portfolio_size
    actions = [
        f"Keeping {len(kept)} holding(s); {open_slots} open slot(s) toward a "
        f"{config.TARGET_PORTFOLIO_MIN}-{config.TARGET_PORTFOLIO_MAX} name portfolio.",
        f"Selected {len(buys)} buy(s) at ~10% each: "
        f"{', '.join(b.symbol for b in buys) if buys else 'none'}.",
    ]
    if unfilled:
        actions.append(
            f"{unfilled} slot(s) unfilled — that capital STAYS IN CASH until next Friday. "
            "The loop never reaches past rank 15 to force a fill.")
    if final_size < config.TARGET_PORTFOLIO_MIN:
        actions.append(
            f"Portfolio would be {final_size} names, below the {config.TARGET_PORTFOLIO_MIN} "
            "minimum — expected when few candidates clear the vetoes.")

    return RiskCapsOutput(
        as_of=as_of,
        target_min=config.TARGET_PORTFOLIO_MIN, target_max=config.TARGET_PORTFOLIO_MAX,
        max_per_sector=config.MAX_PER_SECTOR, max_microcap=config.MAX_MICROCAP,
        kept=kept, open_slots=open_slots, buys=buys, skipped=skipped,
        unfilled_slots=unfilled, sector_counts=sector_counts,
        microcap_count=microcap_count, final_portfolio_size=final_size, actions=actions,
    )


def run(now_ist: dt.datetime | None = None) -> Cycle:
    """Fill open slots under the concentration caps. Pure computation."""
    now_ist = now_ist or dt.datetime.now(config.IST)
    cycle = cycle_service.get_current_cycle()
    cycle_service.require_open(cycle)
    if cycle.current_step != StepId.PART_D_RISK_CAPS.value:
        raise StepNotReady("Step 6 is available only after the technical veto advances.")

    step = cycle.steps.part_d_risk_caps
    step.status = StepStatus.IN_PROGRESS
    step.started_at = now_ist
    step.error = None

    try:
        step.output = _evaluate(cycle)
        step.status = StepStatus.DONE
    except Exception as exc:  # noqa: BLE001
        log.exception("Step 6 failed for cycle %s", cycle.cycle_id)
        step.status = StepStatus.FAILED
        step.output = None
        step.error = f"{type(exc).__name__}: {exc}"
    finally:
        step.completed_at = now_ist

    return cycle_service.touch_and_save(cycle, now_ist)
