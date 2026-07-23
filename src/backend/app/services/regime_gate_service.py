"""Part A business logic — the regime gate (steps 2.1–2.3).

The index and filter are selectable per cycle (config.INDEX_OPTIONS /
FILTER_OPTIONS). Fetch the chosen index's weekly candles, apply the chosen
filter (Supertrend variants, or "none" = always GREEN), decide RED/GREEN. On
RED, mark the ledger for Monday-open liquidation and record the manual "cancel
your GTTs" instruction (no broker call). Writes the result into the current
cycle's Part A envelope; never auto-advances the gate.
"""

from __future__ import annotations

import datetime as dt
import logging

from .. import config
from ..integrations.market_data import fetch_weekly_index_data
from ..domain.strategy import GREEN, RED, decide_regime
from ..repositories import cycles_repository as repo
from ..repositories import ledger_repository as ledger
from ..schemas.common import StepStatus
from ..schemas.cycle import Cycle, RegimeDecision
from ..schemas.regime_gate import RegimeGateOutput
from . import cycle_service

log = logging.getLogger(__name__)

def _compute_output(
    cycle_id: str, index: config.IndexOption, filt: config.FilterOption, now_ist: dt.datetime
) -> RegimeGateOutput:
    # Step 2.1 — weekly candles for the chosen index
    data = fetch_weekly_index_data(index.ticker, now_ist)
    weekly = data.weekly
    week_end = weekly.index[-1].date()

    # Step 2.2/2.3 — apply the chosen filter
    regime, line_label, line_value = decide_regime(weekly, filt)

    previous_regime = repo.get_previous_regime(before_cycle_id=cycle_id)
    reentry_required = regime == GREEN and previous_regime == RED

    log.info("Cycle %s [%s · %s] week %s: close=%.2f -> %s (prev %s)",
             cycle_id, index.label, filt.label, week_end,
             float(weekly["close"].iloc[-1]), regime, previous_regime or "n/a")

    # Running is a pure PREVIEW — it never mutates the ledger. The RED commit
    # (marking holdings, terminating the cycle) happens only on advance.
    actions: list[str] = []
    holdings = ledger.load_ledger()
    active = ledger.active_symbols(holdings)

    if regime == RED:
        actions.append(
            f"On advance: mark {len(active)} holdings PENDING_EXIT for Monday open: {', '.join(active)}"
            if active else "No ACTIVE holdings to mark"
        )
        # v2.2: stops are NOT bulk-cancelled Friday — they protect against a
        # weekend gap. Each GTT is cancelled only as its Monday sell is placed.
        actions.append(
            "Do NOT cancel your GTT stops tonight — leave them live over the weekend. "
            "On Monday, cancel each position's GTT individually just before placing its sell order."
        )
        actions.append("Scan/rank/veto will be skipped — regime RED terminates the ritual")
    else:
        if reentry_required:
            # v2.2: re-entry after RED is implicit — the empty ledger plus Part D
            # slot-filling rebuys naturally. No special-case path.
            actions.append(
                "First GREEN after RED — the ledger is empty, so Part D slot-filling "
                "will rebuild the portfolio normally (no special re-entry step)."
            )
        actions.append("Proceed to Part B (scan, veto, fill slots)")

    return RegimeGateOutput(
        regime=regime,
        week_end=week_end.isoformat(),
        index_close=round(float(weekly["close"].iloc[-1]), 2),
        line_label=line_label,
        line_value=line_value,
        index_id=index.id,
        index_label=index.label,
        index_ticker=index.ticker,
        filter_id=filt.id,
        filter_label=filt.label,
        indicator=filt.indicator,
        data_source=data.source,
        previous_regime=previous_regime,
        reentry_required=reentry_required,
        actions=actions,
        active_holdings=ledger.active_symbols(holdings),
        pending_exit=ledger.pending_exit_symbols(holdings),
    )


def run(
    index_id: str | None = None,
    filter_id: str | None = None,
    now_ist: dt.datetime | None = None,
) -> Cycle:
    """Execute Part A against the current cycle. Idempotent (rerun overwrites).

    Data/compute failure is recorded on the step (status FAILED + error) rather
    than raised, so the UI can render it inline. The chosen index/filter is
    stored on the step's `input` for the journal.
    """
    now_ist = now_ist or dt.datetime.now(config.IST)
    cycle = cycle_service.get_current_cycle()
    cycle_service.require_open(cycle)

    step = cycle.steps.part_a_regime_gate
    step.status = StepStatus.IN_PROGRESS
    step.started_at = now_ist
    step.error = None
    step.input = {
        "index_id": index_id or config.DEFAULT_INDEX_ID,
        "filter_id": filter_id or config.DEFAULT_FILTER_ID,
    }

    try:
        index = config.get_index_option(index_id)
        filt = config.get_filter_option(filter_id)
        out = _compute_output(cycle.cycle_id, index, filt, now_ist)
        step.output = out
        step.status = StepStatus.DONE
        cycle.regime_decision = RegimeDecision(
            index=out.index_label, filter=out.filter_label, regime=out.regime
        )
    except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
        log.exception("Part A failed for cycle %s", cycle.cycle_id)
        step.status = StepStatus.FAILED
        step.output = None
        step.error = f"{type(exc).__name__}: {exc}"
        cycle.regime_decision = None
    finally:
        step.completed_at = now_ist

    return cycle_service.touch_and_save(cycle, now_ist)
