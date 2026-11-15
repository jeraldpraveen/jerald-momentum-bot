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

    # Spec v2.3: the gate is ratified on ONE index — the one every backtest
    # gated on. Another index may still be run (the gate is a playground), but
    # the decision is then outside the tested rules and must say so.
    ratified = config.get_index_option(None)
    ratified_index = index.id == ratified.id
    if not ratified_index:
        actions.append(
            f"UNTESTED GATE — {index.label} is not the ratified regime index "
            f"({ratified.label}). Every backtest gated on {ratified.label}; a decision "
            "from this index is outside the tested rules. Re-run on the ratified index "
            "before acting."
        )

    # The gate must be judging the week this cycle is FOR. If the index feed is
    # behind, the verdict describes an earlier week and must not pass for this
    # week's decision without saying so.
    cycle_week = dt.date.fromisoformat(cycle_id.split(".")[0])
    week_matches_cycle = week_end == cycle_week
    if week_end < cycle_week:
        actions.append(
            f"STALE GATE — the latest closed weekly candle is for the week ending "
            f"{week_end.isoformat()}, older than this cycle's week ({cycle_week.isoformat()}). "
            "This verdict describes an earlier week. Refresh the index data (or wait for "
            "Friday's close) and re-run before acting on it.")
    elif week_end > cycle_week:
        actions.append(
            f"WEEK MISMATCH — the candle judged is for the week ending {week_end.isoformat()}, "
            f"newer than this cycle's week ({cycle_week.isoformat()}). Start a cycle for the "
            "current week rather than deciding an old one on new data.")

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
        # v2.3: RED-week cash is never left idle.
        actions.append(
            f"On Monday, as each sell fills, park the proceeds in your "
            f"{config.PARKING_INSTRUMENT}. They stay there until the first GREEN Friday."
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
        ratified_index=ratified_index,
        week_matches_cycle=week_matches_cycle,
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
