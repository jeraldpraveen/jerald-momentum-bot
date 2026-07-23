"""Part A service — RED/GREEN branch + RED->GREEN re-entry via the cycle store."""

import datetime as dt

import pandas as pd
import pytest

from app.repositories import cycles_repository as repo
from app.schemas.common import StepStatus
from app.services import cycle_service, regime_gate_service
from app.services.errors import StepNotReady
from tests.conftest import green_data, recovery_data, red_data


def _patch(monkeypatch, data):
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: data)


def test_green_run_leaves_ledger_untouched(isolated, monkeypatch, now_ist):
    _patch(monkeypatch, green_data())
    cycle_service.create_cycle(now=now_ist)
    cyc = regime_gate_service.run(now_ist=now_ist)

    step = cyc.steps.part_a_regime_gate
    assert step.status == StepStatus.DONE
    assert step.output.regime == "GREEN"
    # Top-level summary is populated for at-a-glance reading.
    assert cyc.regime_decision is not None
    assert cyc.regime_decision.regime == "GREEN"
    assert cyc.regime_decision.index == "NIFTY MIDSMALLCAP 400"
    assert cyc.regime_decision.filter == "Supertrend (1, 2.5)"
    saved = pd.read_csv(isolated / "holdings_ledger.csv")
    assert (saved["status"] == "ACTIVE").all()


def test_red_run_is_preview_only(isolated, monkeypatch, now_ist):
    _patch(monkeypatch, red_data())
    cycle_service.create_cycle(now=now_ist)
    cyc = regime_gate_service.run(now_ist=now_ist)

    out = cyc.steps.part_a_regime_gate.output
    assert out.regime == "RED"
    # Running is a preview — ledger stays ACTIVE, nothing committed yet.
    assert out.active_holdings == ["AAA", "BBB"]
    assert out.pending_exit == []
    assert any("On advance" in a for a in out.actions)
    saved = pd.read_csv(isolated / "holdings_ledger.csv")
    assert (saved["status"] == "ACTIVE").all()


def test_red_advance_commits_and_short_circuits_to_journal(isolated, monkeypatch, now_ist):
    """v2.2 §2.3: RED skips scan/rank/veto but still writes the journal."""
    _patch(monkeypatch, red_data())
    cycle_service.create_cycle(now=now_ist)
    regime_gate_service.run(now_ist=now_ist)
    cyc = cycle_service.advance_part_a(now=now_ist)

    assert cyc.outcome == "red_terminated"
    assert cyc.current_step == "part_e_journal"          # journal still gets written
    for env in (cyc.steps.part_b_scanner, cyc.steps.part_c_exit_flags,
                cyc.steps.part_d_fundamental, cyc.steps.part_d_technical,
                cyc.steps.part_d_risk_caps):
        assert env.status == StepStatus.SKIPPED
    assert cyc.steps.part_e_journal.status == StepStatus.PENDING
    # Advance is the commit point — NOW the ledger is marked.
    assert cyc.steps.part_a_regime_gate.output.pending_exit == ["AAA", "BBB"]
    saved = pd.read_csv(isolated / "holdings_ledger.csv")
    assert (saved["status"] == "PENDING_EXIT").all()


def test_close_cycle_allows_starting_a_new_one(isolated, monkeypatch, now_ist):
    _patch(monkeypatch, red_data())
    cycle_service.create_cycle(week_end=__import__("datetime").date(2026, 7, 17), now=now_ist)
    regime_gate_service.run(now_ist=now_ist)
    cycle_service.advance_part_a(now=now_ist)          # RED-terminated, "frozen"

    closed = cycle_service.close_current_cycle(now=now_ist)
    assert closed.status == "closed"

    # A fresh cycle can now be started (the dead-end is gone).
    nxt = cycle_service.create_cycle(week_end=__import__("datetime").date(2026, 7, 24), now=now_ist)
    assert nxt.status == "open"
    assert nxt.cycle_id == "2026-07-24"


def test_green_advance_moves_to_part_b(isolated, monkeypatch, now_ist):
    _patch(monkeypatch, green_data())
    cycle_service.create_cycle(now=now_ist)
    regime_gate_service.run(now_ist=now_ist)
    cyc = cycle_service.advance_part_a(now=now_ist)

    assert cyc.outcome is None
    assert cyc.current_step == "part_b_scanner"


def test_advance_before_run_raises(isolated, monkeypatch, now_ist):
    cycle_service.create_cycle(now=now_ist)
    with pytest.raises(StepNotReady):
        cycle_service.advance_part_a(now=now_ist)


def test_reentry_flag_on_red_to_green(isolated, monkeypatch, now_ist):
    # Prior closed cycle = RED.
    _patch(monkeypatch, red_data())
    prior = cycle_service.create_cycle(week_end=dt.date(2026, 7, 10), now=now_ist)
    regime_gate_service.run(now_ist=now_ist)
    prior = repo.load(prior.cycle_id)
    prior.status = "closed"
    repo.save(prior)
    repo.set_current_cycle_id(None)

    # New cycle recovers to GREEN.
    _patch(monkeypatch, recovery_data())
    cycle_service.create_cycle(week_end=dt.date(2026, 7, 17), now=now_ist)
    cyc = regime_gate_service.run(now_ist=now_ist)

    out = cyc.steps.part_a_regime_gate.output
    assert out.regime == "GREEN"
    assert out.previous_regime == "RED"
    assert out.reentry_required is True
    assert any("re-entry" in a for a in out.actions)


def test_always_green_filter_forces_green_and_no_supertrend(isolated, monkeypatch, now_ist):
    # Feed a crashing (RED-shaped) series, but the "none" filter ignores it.
    _patch(monkeypatch, red_data())
    cycle_service.create_cycle(now=now_ist)
    cyc = regime_gate_service.run(filter_id="always_green", now_ist=now_ist)

    out = cyc.steps.part_a_regime_gate.output
    assert out.regime == "GREEN"
    assert out.line_value is None
    assert out.indicator == "none"
    saved = pd.read_csv(isolated / "holdings_ledger.csv")
    assert (saved["status"] == "ACTIVE").all()


def test_selection_stored_on_step_input(isolated, monkeypatch, now_ist):
    _patch(monkeypatch, green_data())
    cycle_service.create_cycle(now=now_ist)
    cyc = regime_gate_service.run(index_id="nifty50", filter_id="st_1_2", now_ist=now_ist)

    step = cyc.steps.part_a_regime_gate
    assert step.input == {"index_id": "nifty50", "filter_id": "st_1_2"}
    assert step.output.index_label == "NIFTY 50"
    assert step.output.filter_label == "Supertrend (1, 2)"


def test_ema_filter_runs_and_labels(isolated, monkeypatch, now_ist):
    _patch(monkeypatch, green_data())  # rising series -> close above EMA
    cycle_service.create_cycle(now=now_ist)
    cyc = regime_gate_service.run(filter_id="ema_34", now_ist=now_ist)
    out = cyc.steps.part_a_regime_gate.output
    assert out.regime == "GREEN"
    assert out.indicator == "ema"
    assert out.line_label == "EMA 34"
    assert out.line_value is not None


def test_macd_filter_runs_and_labels(isolated, monkeypatch, now_ist):
    _patch(monkeypatch, green_data())
    cycle_service.create_cycle(now=now_ist)
    cyc = regime_gate_service.run(filter_id="macd_35_70_12", now_ist=now_ist)
    out = cyc.steps.part_a_regime_gate.output
    assert out.indicator == "macd"
    assert out.line_label == "MACD histogram"


def test_unknown_index_records_failure(isolated, monkeypatch, now_ist):
    _patch(monkeypatch, green_data())
    cycle_service.create_cycle(now=now_ist)
    cyc = regime_gate_service.run(index_id="does_not_exist", now_ist=now_ist)
    assert cyc.steps.part_a_regime_gate.status == StepStatus.FAILED


def test_data_failure_records_failed_step(isolated, monkeypatch, now_ist):
    def boom(*a, **k):
        raise RuntimeError("all providers failed")

    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", boom)
    cycle_service.create_cycle(now=now_ist)
    cyc = regime_gate_service.run(now_ist=now_ist)

    step = cyc.steps.part_a_regime_gate
    assert step.status == StepStatus.FAILED
    assert step.output is None
    assert "all providers failed" in step.error
