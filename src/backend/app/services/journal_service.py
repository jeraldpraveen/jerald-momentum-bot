"""Part E / Step-7 — Journal finalize (Spec v2.2 §2.11).

Compiles the weekly row from every earlier step: regime, holding ranks, exits,
strike-outs with cited reasons, unfilled slots, plus the one manual field —
stops verified (Y/N).

Works for a RED week too, where Parts B-D were skipped: the entry then records
the regime, the holdings marked for liquidation, and nothing else. A record of
going to cash matters as much as a record of trading.

Running compiles a PREVIEW; advancing writes it to the durable journal.
"""

from __future__ import annotations

import datetime as dt
import logging

from .. import config
from ..repositories import journal_repository
from ..repositories import ledger_repository as ledger
from ..schemas.common import StepStatus
from ..schemas.cycle import Cycle, StepId
from ..schemas.journal import (
    JournalBuy,
    JournalEntry,
    JournalHolding,
    JournalOutput,
    JournalRunRequest,
    JournalStrikeout,
)
from . import cycle_service
from .errors import StepNotReady

log = logging.getLogger(__name__)


def _compile(cycle: Cycle, body: JournalRunRequest, now_ist: dt.datetime) -> JournalEntry:
    gate = cycle.steps.part_a_regime_gate.output
    if gate is None:
        raise StepNotReady("Run the regime gate (Step 1) before finalising the journal.")

    audit = cycle.steps.part_c_exit_flags.output      # None on a RED week
    caps = cycle.steps.part_d_risk_caps.output        # None on a RED week
    fund = cycle.steps.part_d_fundamental.output
    tech = cycle.steps.part_d_technical.output

    # Holdings with their ranks and what happens to them.
    holdings: list[JournalHolding] = []
    exits: list[str] = []
    if audit is not None:
        for h in audit.holdings:
            action = "EXIT" if h.exit_flagged else "KEEP"
            holdings.append(JournalHolding(
                symbol=h.symbol, rank=h.rank, action=action,
                reason=", ".join(h.reasons)))
            if h.exit_flagged:
                exits.append(h.symbol)
    else:
        # RED week: everything active was marked for Monday-open liquidation.
        df = ledger.load_ledger()
        marked = df[df["status"] == "PENDING_EXIT"]
        for sym in marked["symbol"]:
            holdings.append(JournalHolding(symbol=str(sym), rank=None, action="EXIT",
                                           reason="regime RED"))
            exits.append(str(sym))

    strikeouts: list[JournalStrikeout] = []
    if fund is not None:
        strikeouts += [
            JournalStrikeout(symbol=c.symbol, rank=c.rank, step="fundamental", reasons=c.strikes)
            for c in fund.candidates if c.struck
        ]
    if tech is not None:
        strikeouts += [
            JournalStrikeout(symbol=c.symbol, rank=c.rank, step="technical", reasons=c.strikes)
            for c in tech.candidates if c.struck
        ]
    if caps is not None:
        strikeouts += [
            JournalStrikeout(symbol=s.symbol, rank=s.rank, step="risk_caps", reasons=[s.reason])
            for s in caps.skipped if s.reason != "slots_full"
        ]

    buys = [JournalBuy(symbol=b.symbol, rank=b.rank, sector=b.sector, weight_pct=b.weight_pct)
            for b in (caps.buys if caps else [])]

    return JournalEntry(
        cycle_id=cycle.cycle_id,
        date=gate.week_end,
        logged_at=now_ist.strftime("%Y-%m-%d %H:%M"),
        regime=gate.regime,
        regime_detail=f"{gate.filter_label} on {gate.index_label}",
        holdings=holdings, exits=exits, buys=buys, strikeouts=strikeouts,
        unfilled_slots=caps.unfilled_slots if caps else 0,
        final_portfolio_size=caps.final_portfolio_size if caps else len(holdings) - len(exits),
        stops_verified=body.stops_verified,
        notes=body.notes,
    )


def run(body: JournalRunRequest, now_ist: dt.datetime | None = None) -> Cycle:
    """Compile the journal row. Preview only — advancing writes it."""
    now_ist = now_ist or dt.datetime.now(config.IST)
    cycle = cycle_service.get_current_cycle()
    cycle_service.require_open(cycle)
    if cycle.current_step != StepId.PART_E_JOURNAL.value:
        raise StepNotReady("Step 7 is available only after the earlier steps advance.")

    step = cycle.steps.part_e_journal
    step.status = StepStatus.IN_PROGRESS
    step.started_at = now_ist
    step.error = None
    step.input = body.model_dump()

    try:
        entry = _compile(cycle, body, now_ist)
        actions = [
            f"{entry.regime} week — {len(entry.exits)} exit(s), {len(entry.buys)} buy(s), "
            f"{len(entry.strikeouts)} strike-out(s) recorded.",
            "On advance: this row is appended to the durable journal "
            "(journal.jsonl + journal.md), which survives cycle deletion.",
        ]
        if body.stops_verified is None:
            actions.append("Stops not yet confirmed — set 'stops verified' before advancing.")
        step.output = JournalOutput(entry=entry, committed=False, actions=actions)
        step.status = StepStatus.DONE
    except Exception as exc:  # noqa: BLE001
        log.exception("Step 7 failed for cycle %s", cycle.cycle_id)
        step.status = StepStatus.FAILED
        step.output = None
        step.error = f"{type(exc).__name__}: {exc}"
    finally:
        step.completed_at = now_ist

    return cycle_service.touch_and_save(cycle, now_ist)


def commit(cycle: Cycle) -> None:
    """Append the compiled entry to the durable journal (called on advance)."""
    out = cycle.steps.part_e_journal.output
    if out is None:
        raise StepNotReady("Compile the journal before advancing.")
    # One row per cycle. Reopening the journal to correct it and advancing again
    # must REPLACE the week's row, not add a second one beside it.
    journal_repository.delete_entries(cycle.cycle_id)
    journal_repository.append(out.entry)
    out.committed = True
    log.info("Journal entry appended for cycle %s", cycle.cycle_id)
