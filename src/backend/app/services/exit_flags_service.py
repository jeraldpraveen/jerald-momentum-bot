"""Part C — Portfolio Audit service.

Cross-reference ACTIVE holdings against the Jerald Score ranking produced in
Part B. Flag a holding for Monday-open exit when it is ranked worse than
RANK_EXIT_THRESHOLD, or when it no longer appears in the scanned universe
(rank = infinity, e.g. dropped from the index).

Running is a pure PREVIEW — the ledger is only written when the user advances.
"""

from __future__ import annotations

import datetime as dt
import logging

from .. import config
from ..domain.strategy import (  # noqa: F401
    DATA_GAP, DROPPED, SCREENED_OUT, Position, audit_holdings,
)
from ..repositories import ledger_repository as ledger
from ..schemas.common import StepStatus
from ..schemas.cycle import Cycle, StepId
from ..schemas.exit_flags import ExitFlagsOutput, HoldingAudit
from . import cycle_service
from .errors import StepNotReady

log = logging.getLogger(__name__)

def _audit(cycle: Cycle) -> ExitFlagsOutput:
    scan = cycle.steps.part_b_scanner.output
    if scan is None:
        raise StepNotReady("Run the Jerald scoring (Step 2) before auditing the portfolio.")

    holdings_df = ledger.load_ledger()
    active = holdings_df[holdings_df["status"] == "ACTIVE"]
    held = [Position(symbol=str(s), sector=str(sec))
            for s, sec in zip(active["symbol"], active["sector"])]

    rows = audit_holdings(held, scan.candidates, rank_threshold=config.RANK_EXIT_THRESHOLD)

    exits = [r.symbol for r in rows if r.exit_flagged]
    if not rows:
        # Distinguish "nothing to audit" from "nothing breached the rules".
        actions = [
            "No ACTIVE holdings to audit — every position is already marked "
            "PENDING_EXIT, or the ledger is empty.",
        ]
    else:
        actions = [
            f"On advance: mark {len(exits)} holding(s) PENDING_EXIT for Monday open: {', '.join(exits)}"
            if exits else "No holdings breach the exit rules — nothing to mark",
            f"Rank exit threshold: > {config.RANK_EXIT_THRESHOLD}",
        ]
    # A holding the scan could not rank is kept, never sold on a guess — but the
    # audit must say it was not audited, or "kept" reads as "checked and fine".
    unpriced = [r.symbol for r in rows if DATA_GAP in r.reasons]
    screened = [r.symbol for r in rows if SCREENED_OUT in r.reasons]
    if unpriced:
        actions.append(
            f"NOT AUDITED — no current price for {', '.join(unpriced)}. Kept, not flagged: "
            "a data gap is not an exit signal. Refresh prices and re-run the scan "
            "before relying on this audit.")
    if screened:
        actions.append(
            f"NOT AUDITED — {', '.join(screened)} fall outside this scan's sector / price "
            "filter. Kept, not flagged. Re-run the scan without the filter to rank them.")
    return ExitFlagsOutput(
        as_of=scan.as_of,
        rank_threshold=config.RANK_EXIT_THRESHOLD,
        universe_label=scan.universe_label,
        holdings=rows,
        exit_count=len(exits),
        keep_count=len(rows) - len(exits),
        actions=actions,
    )


def run(now_ist: dt.datetime | None = None) -> Cycle:
    """Execute Part C against the current cycle. Preview only — never writes the ledger."""
    now_ist = now_ist or dt.datetime.now(config.IST)
    cycle = cycle_service.get_current_cycle()
    cycle_service.require_open(cycle)
    if cycle.current_step != StepId.PART_C_EXIT_FLAGS.value:
        raise StepNotReady("Part C is available only after Part B advances.")

    step = cycle.steps.part_c_exit_flags
    step.status = StepStatus.IN_PROGRESS
    step.started_at = now_ist
    step.error = None

    try:
        step.output = _audit(cycle)
        step.status = StepStatus.DONE
    except Exception as exc:  # noqa: BLE001 — surface failures on the step
        log.exception("Part C failed for cycle %s", cycle.cycle_id)
        step.status = StepStatus.FAILED
        step.output = None
        step.error = f"{type(exc).__name__}: {exc}"
    finally:
        step.completed_at = now_ist

    return cycle_service.touch_and_save(cycle, now_ist)
