"""Cycle lifecycle + the gated state machine.

Enforces the "one active cycle at a time" rule and the RED/GREEN branch on
advancing Part A. Nothing here auto-advances — advancing is always an explicit
user-triggered call (decision: manual, gated workflow).
"""

from __future__ import annotations

import datetime as dt

from .. import config
from ..repositories import cycles_repository as repo
from ..repositories import ledger_repository as ledger
from ..schemas.common import StepStatus
from ..schemas.cycle import GREEN_STEP_ORDER, Cycle, CycleSteps, StepId
from .errors import CycleClosed, CycleConflict, CycleNotFound, NoActiveCycle, StepNotReady


def _now() -> dt.datetime:
    return dt.datetime.now(config.IST)


def _most_recent_friday(today: dt.date) -> dt.date:
    """The Friday on or before `today` (weekday 4 == Friday)."""
    return today - dt.timedelta(days=(today.weekday() - 4) % 7)


def get_current_cycle() -> Cycle:
    cid = repo.get_current_cycle_id()
    if cid is None:
        raise NoActiveCycle("No active cycle. Start the Friday ritual first.")
    try:
        cyc = repo.load(cid)
    except Exception as exc:  # noqa: BLE001 — an incompatible/corrupt file must not 500
        import logging
        logging.getLogger(__name__).warning(
            "Cycle %s failed to load (%s); treating as no active cycle.", cid, type(exc).__name__)
        raise NoActiveCycle(
            f"Current cycle {cid} could not be loaded (incompatible or corrupt). "
            "Start a new cycle.")
    if cyc is None:
        raise NoActiveCycle("Current-cycle pointer is dangling; no cycle file found.")
    return cyc


def get_cycle(cycle_id: str) -> Cycle:
    cyc = repo.load(cycle_id)
    if cyc is None:
        raise CycleNotFound(f"No cycle {cycle_id}.", cycle_id=cycle_id)
    return cyc


def create_cycle(week_end: dt.date | None = None, now: dt.datetime | None = None) -> Cycle:
    """Start a new weekly cycle. 409 if one is already open."""
    now = now or _now()
    current_id = repo.get_current_cycle_id()
    if current_id is not None:
        try:
            existing = repo.load(current_id)
        except Exception as exc:  # noqa: BLE001 — an incompatible file must not block a new cycle
            import logging
            logging.getLogger(__name__).warning(
                "Existing cycle %s failed to load (%s); starting a new one alongside it.",
                current_id, type(exc).__name__)
            existing = None
        if existing is not None and existing.status == "open":
            raise CycleConflict(
                f"Cycle {existing.cycle_id} is still open at {existing.current_step}; "
                "close it before starting a new one.",
                cycle_id=existing.cycle_id,
                current_step=existing.current_step,
            )

    # Base id = the evaluated Friday. If a cycle already exists for that date
    # (e.g. you close and restart within the same week), append a run suffix so
    # the new cycle is distinct and the previous one is kept as history.
    base = (week_end or _most_recent_friday(now.date())).isoformat()
    cycle_id = base
    suffix = 2
    while repo.exists(cycle_id):
        cycle_id = f"{base}.{suffix}"
        suffix += 1

    cycle = Cycle(
        cycle_id=cycle_id,
        created_at=now,
        updated_at=now,
        status="open",
        current_step=StepId.PART_A_REGIME_GATE.value,
        steps=CycleSteps(),
    )
    repo.save(cycle)
    repo.set_current_cycle_id(cycle_id)
    return cycle


def require_open(cycle: Cycle) -> None:
    if cycle.status == "closed":
        raise CycleClosed(
            f"Cycle {cycle.cycle_id} is closed and read-only.", cycle_id=cycle.cycle_id
        )


def touch_and_save(cycle: Cycle, now: dt.datetime | None = None) -> Cycle:
    cycle.updated_at = now or _now()
    return repo.save(cycle)


def _ready_to_advance(cycle: Cycle, step_id: StepId, not_run: str):
    """The step being advanced must have a result AND be the current step.

    The second half matters as much as the first. Advancing is the commit point
    — it marks the ledger and writes the journal — so a repeated or stale
    request (a double click, an old browser tab) must be refused, not allowed
    to drag the cycle back to an earlier step with later results still in place.
    """
    step = getattr(cycle.steps, step_id.value)
    if step.status != StepStatus.DONE or step.output is None:
        raise StepNotReady(not_run)
    if cycle.current_step != step_id.value:
        raise StepNotReady(
            f"{step_id.value} is not the current step — the cycle is at "
            f"{cycle.current_step}. Reopen the step if you mean to change it.",
            current_step=cycle.current_step)
    return step


def advance_part_a(now: dt.datetime | None = None) -> Cycle:
    """User confirms the Part A review; move the gate per the RED/GREEN branch."""
    now = now or _now()
    cycle = get_current_cycle()
    require_open(cycle)

    part_a = _ready_to_advance(cycle, StepId.PART_A_REGIME_GATE,
                               "Run Part A and get a result before advancing.")

    if part_a.output.regime == "RED":
        cycle.outcome = "red_terminated"
        # Commit the RED action now (advance is the commit point): mark all
        # ACTIVE holdings for Monday-open exit. GTT cancel stays manual.
        holdings = ledger.load_ledger()
        holdings, marked = ledger.mark_all_for_liquidation(
            holdings, reason="REGIME_RED", as_of=dt.date.fromisoformat(part_a.output.week_end)
        )
        ledger.save_ledger(holdings)
        part_a.output.active_holdings = ledger.active_symbols(holdings)
        part_a.output.pending_exit = ledger.pending_exit_symbols(holdings)
        # RED short-circuits scan/rank/veto, but the JOURNAL is still written —
        # Spec v2.2 §2.3: "skip scan/rank/veto; write journal; terminate."
        # A record of going to cash matters as much as a record of trading.
        for sid in GREEN_STEP_ORDER[1:-1]:
            if sid == StepId.PART_E_JOURNAL:
                continue
            getattr(cycle.steps, sid.value).status = StepStatus.SKIPPED
        cycle.current_step = StepId.PART_E_JOURNAL.value
    else:
        cycle.current_step = StepId.PART_B_SCANNER.value

    return touch_and_save(cycle, now)


def advance_part_b(now: dt.datetime | None = None) -> Cycle:
    """User confirms the scoring result; move the gate to Part C."""
    now = now or _now()
    cycle = get_current_cycle()
    require_open(cycle)

    _ready_to_advance(cycle, StepId.PART_B_SCANNER,
                      "Run the Jerald scoring and get a result before advancing.")
    cycle.current_step = StepId.PART_C_EXIT_FLAGS.value
    return touch_and_save(cycle, now)


def advance_part_c(now: dt.datetime | None = None) -> Cycle:
    """Commit the portfolio audit: mark flagged holdings for Monday-open exit."""
    now = now or _now()
    cycle = get_current_cycle()
    require_open(cycle)

    step = _ready_to_advance(cycle, StepId.PART_C_EXIT_FLAGS,
                             "Run the portfolio audit and get a result before advancing.")

    flagged = [h for h in step.output.holdings if h.exit_flagged]
    if flagged:
        df = ledger.load_ledger()
        as_of = dt.date.fromisoformat(step.output.as_of)
        for h in flagged:
            match = (df["symbol"] == h.symbol) & (df["status"] == "ACTIVE")
            df.loc[match, "status"] = "PENDING_EXIT"
            df.loc[match, "exit_reason"] = ",".join(h.reasons) or "RANK_EXIT"
            df.loc[match, "marked_on"] = as_of.isoformat()
        ledger.save_ledger(df)

    cycle.current_step = StepId.PART_D_FUNDAMENTAL.value
    return touch_and_save(cycle, now)


def advance_part_d_fundamental(now: dt.datetime | None = None) -> Cycle:
    """Accept the fundamental veto result; move to the technical checks."""
    now = now or _now()
    cycle = get_current_cycle()
    require_open(cycle)

    _ready_to_advance(cycle, StepId.PART_D_FUNDAMENTAL,
                      "Run the fundamental veto and get a result before advancing.")
    cycle.current_step = StepId.PART_D_TECHNICAL.value
    return touch_and_save(cycle, now)


def advance_part_d_technical(now: dt.datetime | None = None) -> Cycle:
    """Accept the technical veto result; move to the risk caps."""
    now = now or _now()
    cycle = get_current_cycle()
    require_open(cycle)

    _ready_to_advance(cycle, StepId.PART_D_TECHNICAL,
                      "Run the technical veto and get a result before advancing.")
    cycle.current_step = StepId.PART_D_RISK_CAPS.value
    return touch_and_save(cycle, now)


def advance_part_d_risk_caps(now: dt.datetime | None = None) -> Cycle:
    """Accept the buy list; move to the journal."""
    now = now or _now()
    cycle = get_current_cycle()
    require_open(cycle)

    _ready_to_advance(cycle, StepId.PART_D_RISK_CAPS,
                      "Run the risk caps and get a result before advancing.")
    cycle.current_step = StepId.PART_E_JOURNAL.value
    return touch_and_save(cycle, now)


def advance_part_e_journal(now: dt.datetime | None = None) -> Cycle:
    """Commit the journal row to the durable log, then move to the Monday plan."""
    now = now or _now()
    cycle = get_current_cycle()
    require_open(cycle)

    _ready_to_advance(cycle, StepId.PART_E_JOURNAL,
                      "Compile the journal and get a result before advancing.")

    from . import journal_service          # local import avoids a cycle
    journal_service.commit(cycle)

    cycle.current_step = StepId.PHASE3_PLAN.value
    return touch_and_save(cycle, now)


def complete_cycle(now: dt.datetime | None = None) -> Cycle:
    """Mark the weekly ritual finished after the Monday plan is acknowledged.

    The cycle stays OPEN (so you can re-read the plan on Monday morning); use
    "Close & start new cycle" when the week is genuinely over.
    """
    now = now or _now()
    cycle = get_current_cycle()
    require_open(cycle)

    _ready_to_advance(cycle, StepId.PHASE3_PLAN,
                      "Build the Monday plan before completing the cycle.")

    cycle.current_step = "done"
    if cycle.outcome is None:
        cycle.outcome = "green_completed"
    return touch_and_save(cycle, now)


def reopen_step(step_id: str, now: dt.datetime | None = None) -> Cycle:
    """Go back to an earlier step to edit and re-run it.

    Everything downstream is invalidated (reset to pending) because it was derived
    from the result you're about to change. NOTE: this cannot un-write ledger
    commits already made by advancing (e.g. holdings marked PENDING_EXIT) — those
    stay until you change them, by design.
    """
    now = now or _now()
    cycle = get_current_cycle()
    require_open(cycle)

    order = [s.value for s in GREEN_STEP_ORDER]
    if step_id not in order:
        raise StepNotReady(f"Unknown step {step_id!r}.")

    target = order.index(step_id)
    for sid in order[target + 1:]:
        env = getattr(cycle.steps, sid)
        env.status = StepStatus.PENDING
        env.output = None
        env.error = None
        env.started_at = None
        env.completed_at = None

    cycle.current_step = step_id
    cycle.outcome = None          # a re-opened cycle is no longer terminated
    return touch_and_save(cycle, now)


def close_current_cycle(now: dt.datetime | None = None) -> Cycle:
    """Close the current cycle and clear the pointer so a new one can start.

    The cycle file is KEPT as read-only history. Used to finish a completed cycle
    or abandon an in-progress one (e.g. after a RED termination).
    """
    cycle = get_current_cycle()
    cycle.status = "closed"
    cycle.current_step = "done"
    saved = touch_and_save(cycle, now)
    repo.set_current_cycle_id(None)
    return saved


def delete_current_cycle() -> tuple[str, int]:
    """Permanently DELETE the current cycle's file (no history kept).

    For discarding throwaway/test cycles, so it also removes the journal rows
    that cycle committed — a discarded run should leave no trace in the
    permanent record. CLOSING a cycle never does this.

    Ledger changes already committed by advancing (holdings marked PENDING_EXIT)
    are NOT reverted: the app cannot know whether you already acted on them.

    Returns (deleted cycle id, journal entries removed).
    """
    cycle_id = repo.get_current_cycle_id()
    if cycle_id is None:
        raise NoActiveCycle("No active cycle to delete.")

    from ..repositories import journal_repository
    removed = journal_repository.delete_entries(cycle_id)

    repo.delete(cycle_id)
    repo.set_current_cycle_id(None)
    return cycle_id, removed
