"""The Friday ritual's state machine and step services — the paths that matter
when something is missing, out of order, or goes wrong.

The cycle is a gated, manual workflow that writes to the ledger and the
permanent journal. These tests ask the awkward questions: what if a step is
advanced twice, what if a file is corrupt, what if a data source is down.
`xfail(strict=True)` marks an answer the Rulebook would not accept.
"""

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from app import config
from app.repositories import cycles_repository as repo
from app.repositories import journal_repository
from app.repositories import ledger_repository as ledger
from app.schemas.common import StepStatus
from app.schemas.fundamental import FundamentalAssessment, PartDFundamentalRunRequest
from app.schemas.holdings import Holding
from app.schemas.journal import JournalRunRequest
from app.schemas.phase3 import Phase3RunRequest
from app.schemas.scanner import PartBRunRequest
from app.services import (
    cycle_service, exit_flags_service, fundamental_service, holdings_service,
    journal_service, phase3_service, regime_gate_service, risk_caps_service,
    scanner_service, technical_service,
)
from app.services.errors import (
    CycleClosed, CycleConflict, CycleNotFound, NoActiveCycle, StepNotReady,
)
from tests.conftest import green_data, red_data
from tests.unit.test_fundamental_service import CANDIDATES, HELD, at_step4  # noqa: F401
from tests.unit.test_journal_service import at_step7, journal_paths  # noqa: F401
from tests.unit.test_phase3_service import at_step8  # noqa: F401
from tests.unit.test_scanner_service import HOLDINGS, _ohlcv, _patch_prices, _seed_holdings, at_part_b  # noqa: F401


def _force(step: str, **outputs):
    """Put the current cycle at `step`, optionally overwriting step outputs."""
    cyc = cycle_service.get_current_cycle()
    cyc.current_step = step
    for name, value in outputs.items():
        getattr(cyc.steps, name).output = value
    return repo.save(cyc)


def _edit_ledger(**values):
    df = ledger.load_ledger()
    for col, val in values.items():
        df[col] = val
    ledger.save_ledger(df)


# =============================================================================
# cycle lifecycle
# =============================================================================

@pytest.mark.parametrize("today,friday", [
    (dt.date(2026, 11, 13), dt.date(2026, 11, 13)),      # Friday -> itself
    (dt.date(2026, 11, 14), dt.date(2026, 11, 13)),      # Saturday
    (dt.date(2026, 11, 15), dt.date(2026, 11, 13)),      # Sunday
    (dt.date(2026, 11, 16), dt.date(2026, 11, 13)),      # Monday
    (dt.date(2026, 11, 19), dt.date(2026, 11, 13)),      # Thursday
    (dt.date(2026, 11, 20), dt.date(2026, 11, 20)),      # the next Friday
])
def test_a_cycle_is_named_after_the_friday_on_or_before_today(today, friday):
    assert cycle_service._most_recent_friday(today) == friday


def test_a_second_cycle_in_the_same_week_gets_a_run_suffix(isolated, now_ist):
    first = cycle_service.create_cycle(now=now_ist)
    cycle_service.close_current_cycle(now=now_ist)
    second = cycle_service.create_cycle(now=now_ist)
    cycle_service.close_current_cycle(now=now_ist)
    third = cycle_service.create_cycle(now=now_ist)

    assert (first.cycle_id, second.cycle_id, third.cycle_id) == (
        "2026-07-17", "2026-07-17.2", "2026-07-17.3")


def test_an_open_cycle_blocks_a_new_one(isolated, now_ist):
    cycle_service.create_cycle(now=now_ist)
    with pytest.raises(CycleConflict) as exc:
        cycle_service.create_cycle(now=now_ist)
    assert exc.value.context == {"cycle_id": "2026-07-17",
                                 "current_step": "part_a_regime_gate"}


def test_a_corrupt_current_cycle_reads_as_no_active_cycle(isolated, now_ist):
    cyc = cycle_service.create_cycle(now=now_ist)
    (isolated / "cycles" / f"{cyc.cycle_id}.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(NoActiveCycle, match="could not be loaded"):
        cycle_service.get_current_cycle()


def test_a_corrupt_current_cycle_does_not_block_starting_a_new_one(isolated, now_ist):
    cyc = cycle_service.create_cycle(now=now_ist)
    (isolated / "cycles" / f"{cyc.cycle_id}.json").write_text("{not json", encoding="utf-8")

    fresh = cycle_service.create_cycle(now=now_ist)
    assert fresh.cycle_id == "2026-07-17.2"                # kept alongside, not overwritten
    assert cycle_service.get_current_cycle().cycle_id == "2026-07-17.2"


def test_a_pointer_to_a_missing_file_reads_as_no_active_cycle(isolated, now_ist):
    repo.set_current_cycle_id("2020-01-03")
    with pytest.raises(NoActiveCycle, match="dangling"):
        cycle_service.get_current_cycle()


def test_get_cycle_by_id(isolated, now_ist):
    cyc = cycle_service.create_cycle(now=now_ist)
    assert cycle_service.get_cycle(cyc.cycle_id).cycle_id == cyc.cycle_id
    with pytest.raises(CycleNotFound) as exc:
        cycle_service.get_cycle("1999-01-01")
    assert exc.value.http_status == 404 and exc.value.context == {"cycle_id": "1999-01-01"}


def test_a_closed_cycle_is_read_only(isolated, now_ist):
    cyc = cycle_service.create_cycle(now=now_ist)
    cycle_service.require_open(cyc)                         # open: fine
    closed = cycle_service.close_current_cycle(now=now_ist)

    assert closed.status == "closed" and closed.current_step == "done"
    with pytest.raises(CycleClosed, match="read-only"):
        cycle_service.require_open(closed)
    with pytest.raises(NoActiveCycle):                      # and the pointer is cleared
        cycle_service.get_current_cycle()


def test_deleting_with_no_cycle_is_an_error(isolated):
    with pytest.raises(NoActiveCycle, match="No active cycle to delete"):
        cycle_service.delete_current_cycle()


def test_deleting_a_cycle_takes_its_journal_row_with_it(at_step7, now_ist):  # noqa: F811
    journal_service.run(JournalRunRequest(stops_verified=True), now_ist=now_ist)
    cycle_service.advance_part_e_journal(now=now_ist)
    assert len(journal_repository.read_entries()) == 1

    cycle_id, removed = cycle_service.delete_current_cycle()

    assert (cycle_id, removed) == ("2026-07-17", 1)
    assert journal_repository.read_entries() == []
    assert not repo.exists(cycle_id)
    assert "2026-07-17" not in (at_step7 / "journal.md").read_text(encoding="utf-8")


def test_closing_a_cycle_keeps_its_journal_row(at_step7, now_ist):  # noqa: F811
    journal_service.run(JournalRunRequest(stops_verified=True), now_ist=now_ist)
    cycle_service.advance_part_e_journal(now=now_ist)
    cycle_service.close_current_cycle(now=now_ist)
    assert len(journal_repository.read_entries()) == 1


# --- advancing ------------------------------------------------------------------

@pytest.mark.parametrize("advance,message", [
    (cycle_service.advance_part_a, "Run Part A"),
    (cycle_service.advance_part_b, "Run the Jerald scoring"),
    (cycle_service.advance_part_c, "Run the portfolio audit"),
    (cycle_service.advance_part_d_fundamental, "Run the fundamental veto"),
    (cycle_service.advance_part_d_technical, "Run the technical veto"),
    (cycle_service.advance_part_d_risk_caps, "Run the risk caps"),
    (cycle_service.advance_part_e_journal, "Compile the journal"),
    (cycle_service.complete_cycle, "Build the Monday plan"),
])
def test_no_step_can_be_advanced_before_it_has_a_result(isolated, now_ist, advance, message):
    cycle_service.create_cycle(now=now_ist)
    with pytest.raises(StepNotReady, match=message):
        advance(now=now_ist)


def test_completing_a_green_cycle_records_the_outcome_and_keeps_it_open(at_step8, now_ist):  # noqa: F811
    phase3_service.run(Phase3RunRequest(), now_ist=now_ist)
    done = cycle_service.complete_cycle(now=now_ist)

    assert done.current_step == "done" and done.outcome == "green_completed"
    assert done.status == "open"                # still readable on Monday morning


# Regression test. Before the fix — FIXED CAVEAT: the advance calls check only that
# THEIR step has a result — never that it is the CURRENT step. Re-sending an earlier
# advance (a double click, a stale browser tab) silently drags the cycle pointer
# backwards while every later step keeps its old output.
def test_advancing_a_step_that_is_not_the_current_one_is_refused(at_step4, now_ist):  # noqa: F811
    assert cycle_service.get_current_cycle().current_step == "part_d_fundamental"
    with pytest.raises(StepNotReady):
        cycle_service.advance_part_a(now=now_ist)


@pytest.mark.parametrize("advance", [
    cycle_service.advance_part_a, cycle_service.advance_part_b, cycle_service.advance_part_c,
])
def test_a_repeated_earlier_advance_changes_nothing(at_step4, now_ist, advance):  # noqa: F811
    """A double click or a stale tab re-sends an advance for a step already
    passed. It is refused, and the cycle stays exactly where it was."""
    before = cycle_service.get_current_cycle()
    with pytest.raises(StepNotReady, match="is not the current step") as exc:
        advance(now=now_ist)

    assert exc.value.context == {"current_step": "part_d_fundamental"}
    after = cycle_service.get_current_cycle()
    assert after.current_step == "part_d_fundamental"
    assert after.updated_at == before.updated_at                 # nothing was written


def test_completing_a_cycle_twice_is_refused(at_step8, now_ist):  # noqa: F811
    phase3_service.run(Phase3RunRequest(), now_ist=now_ist)
    cycle_service.complete_cycle(now=now_ist)
    with pytest.raises(StepNotReady, match="is not the current step"):
        cycle_service.complete_cycle(now=now_ist)


# --- reopening ------------------------------------------------------------------

def test_reopening_a_step_resets_everything_after_it(at_step7, now_ist):  # noqa: F811
    cyc = cycle_service.reopen_step("part_c_exit_flags", now=now_ist)

    assert cyc.current_step == "part_c_exit_flags" and cyc.outcome is None
    assert cyc.steps.part_b_scanner.output is not None          # upstream untouched
    assert cyc.steps.part_c_exit_flags.output is not None       # the step itself kept
    for name in ("part_d_fundamental", "part_d_technical", "part_d_risk_caps",
                 "part_e_journal", "phase3_plan"):
        env = getattr(cyc.steps, name)
        assert env.status == StepStatus.PENDING and env.output is None
        assert env.started_at is None and env.completed_at is None and env.error is None


def test_reopening_an_unknown_step_is_refused(isolated, now_ist):
    cycle_service.create_cycle(now=now_ist)
    with pytest.raises(StepNotReady, match="Unknown step 'nope'"):
        cycle_service.reopen_step("nope", now=now_ist)


def test_caveat_reopening_a_red_week_does_not_unmark_the_ledger(isolated, monkeypatch, now_ist):
    """KNOWN BEHAVIOUR, pinned (the code documents it as by design). A RED advance
    marks every holding PENDING_EXIT. If the gate is then reopened and re-run
    GREEN — a corrected index, a mis-click — the holdings STAY queued for sale:
    Part C only audits ACTIVE names, so nothing puts them back, and Monday's
    plan liquidates the whole book in a GREEN week."""
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: red_data())
    cycle_service.create_cycle(now=now_ist)
    regime_gate_service.run(now_ist=now_ist)
    cycle_service.advance_part_a(now=now_ist)
    assert set(ledger.load_ledger()["status"]) == {"PENDING_EXIT"}

    cycle_service.reopen_step("part_a_regime_gate", now=now_ist)
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: green_data())
    out = regime_gate_service.run(now_ist=now_ist).steps.part_a_regime_gate.output
    cycle_service.advance_part_a(now=now_ist)

    assert out.regime == "GREEN"
    assert set(ledger.load_ledger()["status"]) == {"PENDING_EXIT"}
    assert set(ledger.load_ledger()["exit_reason"]) == {"REGIME_RED"}


# =============================================================================
# Step 1 — regime gate
# =============================================================================

# Regression test. Before the fix — FIXED CAVEAT (stale gate): the gate never compares
# the week it evaluated with the week the cycle is FOR. If the index feed is behind — a
# stale manual file, Yahoo lagging — it returns GREEN or RED for an old week and the
# cycle accepts it without a warning.
def test_the_gate_flags_a_decision_made_on_an_older_week_than_the_cycle(isolated, monkeypatch,
                                                                       now_ist):
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: green_data())
    cyc = cycle_service.create_cycle(now=now_ist)                # cycle 2026-07-17
    out = regime_gate_service.run(now_ist=now_ist).steps.part_a_regime_gate.output

    assert out.week_end < cyc.cycle_id                           # the test data ends in 2024
    assert any("older" in a.lower() or "stale" in a.lower() for a in out.actions)


# =============================================================================
# Step 2 — scoring
# =============================================================================

# Regression test. Before the fix — FIXED CAVEAT: the scan defaults to the 'Current
# Holdings' universe — in the API and in the UI. The Rulebook scans NIFTY MIDSMALLCAP
# 400 + MICROCAP 250. On the default, the ranking is of your own holdings against each
# other: a five-name book can never rank worse than 5, so no rank exit can fire, and
# there is nothing new to buy.
def test_the_default_scan_universe_is_the_two_rulebook_indices():
    assert set(PartBRunRequest().universe_ids) == {"midsmall400", "microcap250"}


# =============================================================================
# Step 3 — portfolio audit
# =============================================================================

def test_the_audit_fails_cleanly_without_a_scan(at_part_b, now_ist):  # noqa: F811
    _force("part_c_exit_flags")
    step = exit_flags_service.run(now_ist=now_ist).steps.part_c_exit_flags

    assert step.status == StepStatus.FAILED and step.output is None
    assert step.error.startswith("StepNotReady: Run the Jerald scoring")


def test_the_audit_says_when_there_is_nothing_to_audit(at_part_b, monkeypatch, now_ist):  # noqa: F811
    _patch_prices(monkeypatch, {f"{s}.NS": _ohlcv(np.linspace(100, 300, 400))
                                for s, _ in HOLDINGS})
    scanner_service.run(PartBRunRequest(universe_ids=["holdings"]), now_ist=now_ist)
    cycle_service.advance_part_b(now=now_ist)
    _edit_ledger(status="PENDING_EXIT")

    out = exit_flags_service.run(now_ist=now_ist).steps.part_c_exit_flags.output
    assert out.holdings == [] and out.exit_count == 0 and out.keep_count == 0
    assert out.actions == ["No ACTIVE holdings to audit — every position is already marked "
                           "PENDING_EXIT, or the ledger is empty."]


def test_a_rank_exit_is_marked_on_the_ledger_only_when_advanced(at_step4, monkeypatch, now_ist):  # noqa: F811
    """End to end: tighten the threshold so the held name breaches it, and check
    the ledger changes on ADVANCE (the commit point), not on run."""
    cycle_service.reopen_step("part_c_exit_flags", now=now_ist)
    monkeypatch.setattr(config, "RANK_EXIT_THRESHOLD", 0)        # even rank 1 breaches

    out = exit_flags_service.run(now_ist=now_ist).steps.part_c_exit_flags.output
    held = out.holdings[0]
    assert held.symbol == "SANSERA" and held.rank == 1 and held.reasons == ["rank_exit"]
    assert set(ledger.load_ledger()["status"]) == {"ACTIVE"}             # preview only

    cycle_service.advance_part_c(now=now_ist)
    row = ledger.load_ledger().iloc[0]
    assert (row["status"], row["exit_reason"]) == ("PENDING_EXIT", "rank_exit")
    assert row["marked_on"] == out.as_of


# =============================================================================
# Step 4 — fundamental veto
# =============================================================================

def _fund(now_ist, **assessments):
    body = PartDFundamentalRunRequest(
        assessments={k: FundamentalAssessment(**v) for k, v in assessments.items()})
    return fundamental_service.run(body, now_ist=now_ist).steps.part_d_fundamental


def test_the_fundamental_veto_fails_cleanly_without_a_scan(at_step4, now_ist):  # noqa: F811
    _force("part_d_fundamental", part_b_scanner=None)
    step = _fund(now_ist)

    assert step.status == StepStatus.FAILED and step.output is None
    assert step.error.startswith("StepNotReady: Run the Jerald scoring")


def test_held_names_are_never_re_vetoed(at_step4, now_ist):  # noqa: F811
    out = _fund(now_ist).output
    assert "SANSERA" not in [c.symbol for c in out.candidates]
    assert [c.symbol for c in out.candidates] == ["ALPHA", "BETA", "GAMMA", "DELTA"]
    assert [c.rank for c in out.candidates] == [2, 3, 4, 5]          # rank order, 1 is held


def test_caveat_a_name_still_queued_for_exit_is_offered_as_a_new_buy(at_step4, now_ist):  # noqa: F811
    """KNOWN BEHAVIOUR, pinned: the buy pool excludes ACTIVE holdings only. A
    position marked PENDING_EXIT in an earlier week and not yet sold (or not yet
    updated in the ledger) is no longer 'held' to this step — if it ranks in the
    top 15 it comes back as a fresh candidate, and the Monday plan can show the
    same name under both SELL and BUY."""
    df = ledger.load_ledger()
    df["status"], df["exit_reason"] = "PENDING_EXIT", "rank_exit"
    ledger.save_ledger(df)

    out = _fund(now_ist).output
    assert out.candidates[0].symbol == "SANSERA" and out.candidates[0].rank == 1


def test_a_stock_under_surveillance_is_struck_whatever_is_typed_in(at_step4, monkeypatch,  # noqa: F811
                                                                 now_ist):
    monkeypatch.setattr(fundamental_service.surveillance, "fetch_surveillance",
                        lambda *a, **k: {"ALPHA": "NSE ASM (Stage I)"})
    row = next(c for c in _fund(now_ist, ALPHA={"pending_action": False}).output.candidates
               if c.symbol == "ALPHA")

    assert row.struck and row.strikes == ["under NSE ASM (Stage I)"]


# Regression test. Before the fix — FIXED CAVEAT (false citation): when the NSE
# surveillance lists could not be fetched, the veto still writes 'not on ASM/GSM'
# against every candidate — and with the manual box set to 'none pending' it records a
# clean PASS. That is a check being cited that never ran.
def test_an_unreachable_surveillance_list_is_reported_as_not_checked(at_step4, monkeypatch,  # noqa: F811
                                                                    now_ist):
    monkeypatch.setattr(fundamental_service.surveillance, "fetch_surveillance",
                        lambda *a, **k: None)                # None = NSE could not be reached
    out = _fund(now_ist, ALPHA={"pending_action": False}).output
    row = out.candidates[0]

    assert row.pending_action.verdict == "unverified"
    assert "not on ASM/GSM" not in row.pending_action.detail
    assert row.pending_action.detail == "NSE surveillance lists unavailable — ASM/GSM NOT checked"
    assert out.surveillance_checked is False
    assert any("did NOT run this week" in a for a in out.actions)
    assert not row.struck                                     # unverified passes, flagged


def test_a_manually_entered_pending_action_still_strikes_when_nse_is_unreachable(
        at_step4, monkeypatch, now_ist):  # noqa: F811
    monkeypatch.setattr(fundamental_service.surveillance, "fetch_surveillance",
                        lambda *a, **k: None)
    row = _fund(now_ist, ALPHA={"pending_action": True}).output.candidates[0]
    assert row.struck and row.pending_action.verdict == "strike"


def test_a_reachable_list_with_nothing_on_it_is_a_real_all_clear(at_step4, now_ist):  # noqa: F811
    out = _fund(now_ist, ALPHA={"pending_action": False}).output       # fixture returns {}
    row = out.candidates[0]
    assert out.surveillance_checked is True
    assert (row.pending_action.verdict, row.pending_action.detail) == (
        "pass", "not on ASM/GSM; none pending")
    assert not any("did NOT run" in a for a in out.actions)


# Regression test. Before the fix — FIXED CAVEAT (dead control): the request accepts
# `refresh_auto` ('re-pull interest coverage from Yahoo') and nothing reads it. The
# cached figure — up to 30 days old, or a cached failure — is used regardless.
def test_refresh_auto_forces_a_new_interest_coverage_pull(at_step4, monkeypatch, now_ist):  # noqa: F811
    seen = []
    monkeypatch.setattr(fundamental_service.fundamentals, "get_interest_coverage",
                        lambda ticker, *a, **kw: seen.append(kw) or 3.0)
    fundamental_service.run(PartDFundamentalRunRequest(refresh_auto=True), now_ist=now_ist)

    assert seen and all(kw == {"refresh": True} for kw in seen)

    seen.clear()
    fundamental_service.run(PartDFundamentalRunRequest(), now_ist=now_ist)
    assert seen and all(kw == {"refresh": False} for kw in seen)   # default: use the cache


# =============================================================================
# Step 5 — technical veto
# =============================================================================

def test_the_technical_veto_fails_cleanly_without_step_4(at_step4, now_ist):  # noqa: F811
    _force("part_d_technical")
    step = technical_service.run(now_ist=now_ist).steps.part_d_technical

    assert step.status == StepStatus.FAILED
    assert step.error.startswith("StepNotReady: Run the fundamental veto")


# =============================================================================
# Step 6 — risk caps
# =============================================================================

def test_the_risk_caps_fail_cleanly_without_step_5(at_step4, now_ist):  # noqa: F811
    _force("part_d_risk_caps")
    step = risk_caps_service.run(now_ist=now_ist).steps.part_d_risk_caps

    assert step.status == StepStatus.FAILED
    assert step.error.startswith("StepNotReady: Run the technical veto")


def test_microcap_symbols_come_from_the_microcap_250_list(monkeypatch):
    monkeypatch.setattr(
        risk_caps_service.constituents, "fetch_constituents",
        lambda key: pd.DataFrame({"symbol": [" sansera ", "Tiny"], "sector": ["A", "B"]}))
    assert risk_caps_service._microcap_symbols() == {"SANSERA", "TINY"}


def test_microcap_symbols_are_empty_when_the_list_cannot_be_fetched(monkeypatch):
    def boom(key):
        raise ConnectionError("nse down")

    monkeypatch.setattr(risk_caps_service.constituents, "fetch_constituents", boom)
    assert risk_caps_service._microcap_symbols() is None       # "unknown", not "none"


# Regression test. Before the fix — FIXED CAVEAT (data-safeguard): if the Microcap 250
# list cannot be fetched, the max-4-microcaps cap silently stops being enforced. The
# step still reports 'max_microcap: 4' and a microcap count of 0, with nothing telling
# you the cap did not run — it is only written to the server log.
def test_an_unavailable_microcap_list_is_reported_on_the_step(at_step4, monkeypatch, now_ist):  # noqa: F811
    fundamental_service.run(PartDFundamentalRunRequest(), now_ist=now_ist)
    cycle_service.advance_part_d_fundamental(now=now_ist)
    monkeypatch.setattr(technical_service.stock_prices, "get_prices",
                        lambda tickers, as_of=None, today=None: {})
    technical_service.run(now_ist=now_ist)
    cycle_service.advance_part_d_technical(now=now_ist)

    def boom(key):
        raise ConnectionError("nse down")

    monkeypatch.setattr(risk_caps_service.constituents, "fetch_constituents", boom)
    out = risk_caps_service.run(now_ist=now_ist).steps.part_d_risk_caps.output

    assert any("microcap" in a.lower() and "not" in a.lower() for a in out.actions)
    assert out.microcap_cap_enforced is False
    assert any("was NOT enforced" in a and "by hand" in a for a in out.actions)


# =============================================================================
# Step 7 — journal
# =============================================================================

def test_the_journal_fails_cleanly_without_a_regime_decision(at_step7, now_ist):  # noqa: F811
    _force("part_e_journal", part_a_regime_gate=None)
    step = journal_service.run(JournalRunRequest(), now_ist=now_ist).steps.part_e_journal

    assert step.status == StepStatus.FAILED and step.output is None
    assert step.error.startswith("StepNotReady: Run the regime gate")


def test_committing_without_a_compiled_entry_is_refused(isolated, now_ist):
    cyc = cycle_service.create_cycle(now=now_ist)
    with pytest.raises(StepNotReady, match="Compile the journal before advancing"):
        journal_service.commit(cyc)


def test_a_rank_exit_is_recorded_in_the_journal_with_its_reason(at_step4, journal_paths,  # noqa: F811
                                                               monkeypatch, now_ist):
    cycle_service.reopen_step("part_c_exit_flags", now=now_ist)
    monkeypatch.setattr(config, "RANK_EXIT_THRESHOLD", 0)        # even rank 1 breaches
    exit_flags_service.run(now_ist=now_ist)
    cycle_service.advance_part_c(now=now_ist)
    fundamental_service.run(PartDFundamentalRunRequest(), now_ist=now_ist)
    cycle_service.advance_part_d_fundamental(now=now_ist)
    monkeypatch.setattr(technical_service.stock_prices, "get_prices",
                        lambda tickers, as_of=None, today=None: {})
    technical_service.run(now_ist=now_ist)
    cycle_service.advance_part_d_technical(now=now_ist)
    monkeypatch.setattr(risk_caps_service, "_microcap_symbols", lambda: set())
    risk_caps_service.run(now_ist=now_ist)
    cycle_service.advance_part_d_risk_caps(now=now_ist)

    entry = journal_service.run(JournalRunRequest(stops_verified=False),
                                now_ist=now_ist).steps.part_e_journal.output.entry

    assert entry.exits == ["SANSERA"]
    assert [(h.symbol, h.rank, h.action, h.reason) for h in entry.holdings] == [
        ("SANSERA", 1, "EXIT", "rank_exit")]
    assert entry.stops_verified is False
    assert {b.symbol for b in entry.buys} >= {s for s, _ in CANDIDATES}


# Regression test. Before the fix — FIXED CAVEAT (permanent record): every journal
# advance APPENDS a row. Reopen the journal step to fix a note and advance again — or
# double-click — and the same week is in the journal twice, with nothing marking which
# row stands.
def test_advancing_the_journal_twice_leaves_one_row_for_the_week(at_step7, now_ist):  # noqa: F811
    journal_service.run(JournalRunRequest(stops_verified=True, notes="first"), now_ist=now_ist)
    cycle_service.advance_part_e_journal(now=now_ist)
    cycle_service.reopen_step("part_e_journal", now=now_ist)
    journal_service.run(JournalRunRequest(stops_verified=True, notes="corrected"), now_ist=now_ist)
    cycle_service.advance_part_e_journal(now=now_ist)

    rows = journal_repository.read_entries()
    assert len(rows) == 1 and rows[0].notes == "corrected"


def test_caveat_a_red_week_journal_labels_every_pending_exit_as_regime_red(
        isolated, journal_paths, monkeypatch, now_ist):  # noqa: F811
    """KNOWN BEHAVIOUR, pinned: on a RED week the journal lists everything that is
    PENDING_EXIT in the ledger and calls it 'regime RED' — including a name that
    was already queued for a rank exit in an earlier week and not yet sold."""
    df = ledger.load_ledger()
    df.loc[df["symbol"] == "AAA", ["status", "exit_reason"]] = ["PENDING_EXIT", "rank_exit"]
    ledger.save_ledger(df)
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: red_data())
    cycle_service.create_cycle(now=now_ist)
    regime_gate_service.run(now_ist=now_ist)
    cycle_service.advance_part_a(now=now_ist)

    entry = journal_service.run(JournalRunRequest(), now_ist=now_ist).steps.part_e_journal.output.entry
    assert {(h.symbol, h.reason) for h in entry.holdings} == {
        ("AAA", "regime RED"), ("BBB", "regime RED")}
    assert entry.final_portfolio_size == 0 and entry.unfilled_slots == 0


# =============================================================================
# holdings (the ledger API)
# =============================================================================

def test_valuation_survives_a_price_feed_failure(isolated, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("yahoo down")

    monkeypatch.setattr(holdings_service.stock_prices, "get_prices", boom)
    summary = holdings_service.summarize()

    assert summary.market_value is None and summary.unpriced == ["AAA", "BBB"]
    assert summary.capital_deployed == pytest.approx(10 * 100 + 20 * 200)
    assert any("No price for AAA, BBB" in w for w in summary.warnings)


def test_no_held_positions_asks_for_no_prices(isolated, monkeypatch):
    monkeypatch.setattr(holdings_service.stock_prices, "get_prices",
                        lambda *a, **k: pytest.fail("nothing to price"))
    assert holdings_service._last_closes([]) == {}


def test_looking_up_a_blank_symbol_finds_nothing():
    assert holdings_service.lookup_symbol("   ") == holdings_service.SymbolLookup(
        symbol="", found=False)


def _universe(monkeypatch, tables, prices=None):
    def fetch(key):
        if isinstance(tables.get(key), Exception):
            raise tables[key]
        return tables.get(key, pd.DataFrame({"symbol": [], "sector": []}))

    monkeypatch.setattr(holdings_service.constituents, "fetch_constituents", fetch)
    monkeypatch.setattr(holdings_service.stock_prices, "get_prices",
                        prices or (lambda tickers, **k: {}))


def test_lookup_takes_sector_and_sleeve_from_the_first_index_listing_the_name(monkeypatch):
    micro = config.get_universe_option("microcap250").nse_key
    _universe(monkeypatch,
              {micro: pd.DataFrame({"symbol": ["sansera"],
                                    "sector": ["Automobile and Auto Components"]})},
              prices=lambda tickers, **k: {"SANSERA.NS": _ohlcv([100.0, 123.456])})

    out = holdings_service.lookup_symbol(" sansera ")
    assert (out.symbol, out.found) == ("SANSERA", True)
    assert out.sector == "Automobile and Auto Components"
    assert out.sleeve == "MICROCAP250" and out.last_close == 123.46


def test_lookup_labels_a_broader_index_member_with_that_index(monkeypatch):
    n500 = config.get_universe_option("nifty500").nse_key
    _universe(monkeypatch, {n500: pd.DataFrame({"symbol": ["BIGCO"], "sector": ["IT"]})})
    out = holdings_service.lookup_symbol("BIGCO")
    assert (out.found, out.sleeve, out.last_close) == (True, "NIFTY 500", None)


def test_lookup_survives_a_failing_index_and_a_failing_price_feed(monkeypatch):
    mid = config.get_universe_option("midsmall400").nse_key

    def boom(tickers, **k):
        raise ConnectionError("yahoo down")

    _universe(monkeypatch, {mid: ConnectionError("nse down")}, prices=boom)
    out = holdings_service.lookup_symbol("UNKNOWN")
    assert (out.found, out.sector, out.sleeve, out.last_close) == (False, "", "", None)


def test_lookup_skips_universes_that_are_not_index_lists(monkeypatch):
    monkeypatch.setattr(holdings_service, "_LOOKUP_ORDER", ["holdings", "nifty50"])
    asked = []
    monkeypatch.setattr(holdings_service.constituents, "fetch_constituents",
                        lambda key: asked.append(key) or pd.DataFrame({"symbol": [], "sector": []}))
    monkeypatch.setattr(holdings_service.stock_prices, "get_prices", lambda tickers, **k: {})

    holdings_service.lookup_symbol("X")
    assert asked == [config.get_universe_option("nifty50").nse_key]


def test_a_name_found_only_by_price_still_counts_as_found(monkeypatch):
    _universe(monkeypatch, {}, prices=lambda tickers, **k: {"ODD.NS": _ohlcv([50.0, 55.0])})
    out = holdings_service.lookup_symbol("ODD")
    assert (out.found, out.sector, out.last_close) == (True, "", 55.0)


def test_saving_holdings_normalises_symbols_and_clears_exit_fields_on_active(isolated):
    saved = holdings_service.save_holdings([
        Holding(symbol=" tmb ", qty=10, entry_price=100, status="ACTIVE",
                exit_reason="rank_exit", marked_on="2026-07-26", exit_price=120.0,
                exit_charges=5.0),
        Holding(symbol="SOLD", qty=5, entry_price=50, status="EXITED",
                exit_reason="rank_exit", marked_on="2026-07-26", exit_price=60.0),
    ])

    active, exited = saved
    assert active.symbol == "TMB"
    assert (active.exit_reason, active.marked_on, active.exit_price, active.exit_charges) == \
        ("", "", None, None)
    assert (exited.exit_reason, exited.exit_price) == ("rank_exit", 60.0)


# =============================================================================
# repositories
# =============================================================================

def test_cycle_repository_reads_of_things_that_are_not_there(isolated):
    assert repo.load("1999-01-01") is None
    assert repo.delete("1999-01-01") is False
    assert repo.list_cycles() == []                         # no cycles directory yet
    assert repo.get_current_cycle_id() is None


def test_an_interrupted_cycle_write_leaves_the_old_file_and_no_temp_file(isolated, monkeypatch,
                                                                       now_ist):
    cyc = cycle_service.create_cycle(now=now_ist)
    path = isolated / "cycles" / f"{cyc.cycle_id}.json"
    before = path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(repo.os, "replace", boom)
    cyc.current_step = "done"
    with pytest.raises(OSError, match="disk full"):
        repo.save(cyc)

    assert path.read_text(encoding="utf-8") == before
    assert list((isolated / "cycles").glob("*.tmp")) == []


def test_an_unreadable_cycle_file_does_not_hide_the_rest_of_history(isolated, now_ist):
    cycle_service.create_cycle(now=now_ist)
    (isolated / "cycles" / "2020-01-03.json").write_text("{broken", encoding="utf-8")
    assert [s.cycle_id for s in repo.list_cycles()] == ["2026-07-17"]


def test_previous_regime_skips_a_cycle_that_vanished_mid_read(isolated, monkeypatch, now_ist):
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: red_data())
    cycle_service.create_cycle(week_end=dt.date(2026, 7, 10), now=now_ist)
    regime_gate_service.run(now_ist=now_ist)
    assert repo.get_previous_regime("2026-07-17") == "RED"

    monkeypatch.setattr(repo, "load", lambda cid: None)       # deleted between list and load
    assert repo.get_previous_regime("2026-07-17") is None


def test_previous_regime_ignores_a_prior_cycle_whose_gate_never_ran(isolated, now_ist):
    cycle_service.create_cycle(week_end=dt.date(2026, 7, 10), now=now_ist)
    assert repo.get_previous_regime("2026-07-17") is None


def test_journal_reads_and_deletes_are_safe_with_no_journal(journal_paths):  # noqa: F811
    assert journal_repository.read_entries() == []
    assert journal_repository.delete_entries("2026-07-17") == 0


def test_deleting_a_cycle_that_wrote_no_journal_row_changes_nothing(at_step7, now_ist):  # noqa: F811
    journal_service.run(JournalRunRequest(stops_verified=True), now_ist=now_ist)
    cycle_service.advance_part_e_journal(now=now_ist)
    before = (at_step7 / "journal.jsonl").read_text(encoding="utf-8")

    assert journal_repository.delete_entries("1999-01-01") == 0
    assert (at_step7 / "journal.jsonl").read_text(encoding="utf-8") == before


def test_journal_limit_returns_the_most_recent_rows_first(at_step7, now_ist):  # noqa: F811
    entry = journal_service.run(JournalRunRequest(), now_ist=now_ist).steps.part_e_journal.output.entry
    for week in ("2026-07-03", "2026-07-10", "2026-07-17"):
        journal_repository.append(entry.model_copy(update={"cycle_id": week, "date": week}))

    assert [e.cycle_id for e in journal_repository.read_entries()] == [
        "2026-07-17", "2026-07-10", "2026-07-03"]
    assert [e.cycle_id for e in journal_repository.read_entries(limit=1)] == ["2026-07-17"]


def test_journal_markdown_cells_cannot_break_the_table(at_step7, now_ist):  # noqa: F811
    entry = journal_service.run(JournalRunRequest(notes="a | b\nc"),
                                now_ist=now_ist).steps.part_e_journal.output.entry
    row = journal_repository._md_row(entry)

    assert "a / b c" in row and row.count("\n") == 1
    assert journal_repository._cell("") == "—"


def test_the_ledger_is_empty_when_there_is_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOLDINGS_LEDGER", tmp_path / "holdings_ledger.csv")
    df = ledger.load_ledger()
    assert df.empty and list(df.columns) == ledger.COLUMNS
    ledger._snapshot()                                       # nothing to back up: no error
    assert not (tmp_path / "ledger_backups").exists()


def test_ledger_numbers_that_cannot_be_parsed_read_as_blank():
    assert ledger._num("abc", float) is None
    assert ledger._num("", int) is None and ledger._num(None, int) is None
    assert ledger._num(float("nan"), float) is None
    assert ledger._num("12.9", int) == 12 and ledger._num("12.5", float) == 12.5


def test_restoring_a_backup_that_does_not_exist_is_refused(isolated):
    assert ledger.restore_backup("holdings_ledger.19990101-000000-000.csv") is False


def test_a_ledger_with_the_wrong_columns_is_never_written(isolated):
    with pytest.raises(ValueError, match="unexpected columns"):
        ledger.save_ledger(pd.DataFrame({"symbol": ["X"]}))
    assert "AAA" in (isolated / "holdings_ledger.csv").read_text(encoding="utf-8")


# =============================================================================
# more on the fixed caveats
# =============================================================================

def test_the_gate_is_quiet_when_it_judged_the_cycles_own_week(isolated, monkeypatch, now_ist):
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: green_data())
    cycle_service.create_cycle(week_end=dt.date(2024, 10, 4), now=now_ist)   # the data's last week
    out = regime_gate_service.run(now_ist=now_ist).steps.part_a_regime_gate.output

    assert out.week_end == "2024-10-04" and out.week_matches_cycle is True
    assert not any("STALE GATE" in a or "WEEK MISMATCH" in a for a in out.actions)


def test_a_stale_gate_names_both_weeks(isolated, monkeypatch, now_ist):
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: green_data())
    cycle_service.create_cycle(now=now_ist)
    out = regime_gate_service.run(now_ist=now_ist).steps.part_a_regime_gate.output

    assert out.week_matches_cycle is False
    stale = next(a for a in out.actions if a.startswith("STALE GATE"))
    assert "2024-10-04" in stale and "2026-07-17" in stale and "re-run" in stale


def test_a_back_dated_cycle_is_not_decided_on_newer_candles_silently(isolated, monkeypatch,
                                                                    now_ist):
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: green_data())
    cycle_service.create_cycle(week_end=dt.date(2024, 1, 5), now=now_ist)
    out = regime_gate_service.run(now_ist=now_ist).steps.part_a_regime_gate.output

    assert out.week_matches_cycle is False
    assert any(a.startswith("WEEK MISMATCH") and "newer" in a for a in out.actions)


def test_a_second_run_in_the_same_week_is_matched_on_its_date_not_its_suffix(
        isolated, monkeypatch, now_ist):
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: green_data())
    cycle_service.create_cycle(week_end=dt.date(2024, 10, 4), now=now_ist)
    cycle_service.close_current_cycle(now=now_ist)
    second = cycle_service.create_cycle(week_end=dt.date(2024, 10, 4), now=now_ist)
    out = regime_gate_service.run(now_ist=now_ist).steps.part_a_regime_gate.output

    assert second.cycle_id == "2024-10-04.2" and out.week_matches_cycle is True


def test_the_scan_defaults_to_the_rulebook_indices_everywhere(at_part_b, monkeypatch, now_ist):  # noqa: F811
    asked = []
    monkeypatch.setattr(scanner_service, "_resolve_universes",
                        lambda ids: asked.append(list(ids)) or [])
    monkeypatch.setattr(scanner_service.stock_prices, "get_prices", lambda *a, **k: {})

    scanner_service.run(PartBRunRequest(universe_ids=[]), now_ist=now_ist)      # blank
    scanner_service.run(PartBRunRequest(), now_ist=now_ist)                     # omitted
    scanner_service.list_sectors([])

    assert asked == [["midsmall400", "microcap250"]] * 3
    assert config.DEFAULT_UNIVERSE_IDS == ("midsmall400", "microcap250")


def test_an_available_microcap_list_is_reported_as_enforced(at_step4, monkeypatch, now_ist):  # noqa: F811
    fundamental_service.run(PartDFundamentalRunRequest(), now_ist=now_ist)
    cycle_service.advance_part_d_fundamental(now=now_ist)
    monkeypatch.setattr(technical_service.stock_prices, "get_prices",
                        lambda tickers, as_of=None, today=None: {})
    technical_service.run(now_ist=now_ist)
    cycle_service.advance_part_d_technical(now=now_ist)
    monkeypatch.setattr(risk_caps_service, "_microcap_symbols", lambda: {"ALPHA"})

    out = risk_caps_service.run(now_ist=now_ist).steps.part_d_risk_caps.output
    assert out.microcap_cap_enforced is True and out.microcap_count == 1
    assert not any("NOT enforced" in a for a in out.actions)


def test_a_corrected_journal_row_replaces_the_old_one_in_both_files(at_step7, now_ist):  # noqa: F811
    journal_service.run(JournalRunRequest(stops_verified=False, notes="first"), now_ist=now_ist)
    cycle_service.advance_part_e_journal(now=now_ist)
    cycle_service.reopen_step("part_e_journal", now=now_ist)
    journal_service.run(JournalRunRequest(stops_verified=True, notes="corrected"), now_ist=now_ist)
    cycle_service.advance_part_e_journal(now=now_ist)

    md = (at_step7 / "journal.md").read_text(encoding="utf-8")
    assert md.count("| 2026-07-17 |") == 1 and "corrected" in md and "first" not in md
    assert len((at_step7 / "journal.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_another_weeks_journal_row_is_untouched_by_a_re_advance(at_step7, now_ist):  # noqa: F811
    entry = journal_service.run(JournalRunRequest(), now_ist=now_ist).steps.part_e_journal.output.entry
    journal_repository.append(entry.model_copy(update={"cycle_id": "2026-07-10",
                                                       "date": "2026-07-10"}))
    cycle_service.advance_part_e_journal(now=now_ist)
    cycle_service.reopen_step("part_e_journal", now=now_ist)
    journal_service.run(JournalRunRequest(notes="again"), now_ist=now_ist)
    cycle_service.advance_part_e_journal(now=now_ist)

    assert [e.cycle_id for e in journal_repository.read_entries()] == ["2026-07-17", "2026-07-10"]
