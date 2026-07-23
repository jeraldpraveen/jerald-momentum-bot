"""Step-7 journal tests (Spec v2.2 §2.11) — including the RED-week record."""

import json

import numpy as np
import pandas as pd
import pytest

from app import config
from app.repositories import journal_repository
from app.schemas.common import StepStatus
from app.schemas.fundamental import PartDFundamentalRunRequest
from app.schemas.journal import JournalRunRequest
from app.services import (
    cycle_service,
    fundamental_service,
    journal_service,
    regime_gate_service,
    risk_caps_service,
    technical_service,
)
from app.services.errors import StepNotReady
from tests.conftest import red_data
from tests.unit.test_fundamental_service import CANDIDATES, at_step4  # noqa: F401
from tests.unit.test_scanner_service import HOLDINGS, _ohlcv, _seed_holdings


@pytest.fixture
def journal_paths(isolated, monkeypatch):
    monkeypatch.setattr(config, "JOURNAL_JSONL", isolated / "journal.jsonl")
    monkeypatch.setattr(config, "JOURNAL_MD", isolated / "journal.md")
    return isolated


@pytest.fixture
def at_step7(at_step4, journal_paths, monkeypatch, now_ist):  # noqa: F811
    """Drive a GREEN cycle all the way to the journal step."""
    fundamental_service.run(PartDFundamentalRunRequest(), now_ist=now_ist)
    cycle_service.advance_part_d_fundamental(now=now_ist)
    rng = np.random.default_rng(7)
    healthy = _ohlcv(100 * np.cumprod(1 + rng.normal(0.0012, 0.012, 300)))
    monkeypatch.setattr(technical_service.stock_prices, "get_prices",
                        lambda tickers, as_of=None, today=None: {t: healthy for t in tickers})
    technical_service.run(now_ist=now_ist)
    cycle_service.advance_part_d_technical(now=now_ist)
    monkeypatch.setattr(risk_caps_service, "_microcap_symbols", lambda: set())
    risk_caps_service.run(now_ist=now_ist)
    cycle_service.advance_part_d_risk_caps(now=now_ist)
    return journal_paths


def _run(now_ist, **kw):
    return journal_service.run(JournalRunRequest(**kw), now_ist=now_ist)


def test_requires_step7(at_step4, now_ist):  # noqa: F811
    with pytest.raises(StepNotReady):
        _run(now_ist)


def test_entry_compiles_from_all_steps(at_step7, now_ist):
    out = _run(now_ist, stops_verified=True, notes="all good").steps.part_e_journal.output
    e = out.entry

    assert e.regime == "GREEN"
    assert "on" in e.regime_detail                 # "<filter> on <index>"
    assert e.holdings                              # ranks from the Part C audit
    assert e.stops_verified is True and e.notes == "all good"
    assert out.committed is False                  # run is a preview


def test_strikeouts_carry_cited_reasons(at_step7, now_ist):
    out = _run(now_ist).steps.part_e_journal.output
    for s in out.entry.strikeouts:
        assert s.reasons, f"{s.symbol} struck without a cited reason"
        assert s.step in {"fundamental", "technical", "risk_caps"}


def test_advance_appends_to_durable_journal(at_step7, now_ist):
    _run(now_ist, stops_verified=True, notes="week 1")
    cyc = cycle_service.advance_part_e_journal(now=now_ist)

    assert cyc.current_step == "phase3_plan"
    assert cyc.steps.part_e_journal.output.committed is True

    rows = [json.loads(l) for l in config.JOURNAL_JSONL.read_text().splitlines() if l.strip()]
    assert len(rows) == 1 and rows[0]["notes"] == "week 1"
    md = config.JOURNAL_MD.read_text(encoding="utf-8")
    assert "Strategy Journal" in md and "GREEN" in md


def test_journal_survives_closing_a_cycle(at_step7, now_ist):
    """CLOSING keeps the record — the cycle is finished, not discarded."""
    _run(now_ist, stops_verified=True)
    cycle_service.advance_part_e_journal(now=now_ist)
    cycle_service.close_current_cycle(now=now_ist)

    assert len(journal_repository.read_entries()) == 1


def test_deleting_a_cycle_removes_its_journal_rows(at_step7, now_ist):
    """DELETING discards a throwaway run, so it must leave no trace behind."""
    _run(now_ist, stops_verified=True)
    cycle = cycle_service.advance_part_e_journal(now=now_ist)
    assert len(journal_repository.read_entries()) == 1

    _, removed = cycle_service.delete_current_cycle()

    assert removed == 1
    assert journal_repository.read_entries() == []
    md = config.JOURNAL_MD.read_text(encoding="utf-8")
    assert cycle.cycle_id not in md                  # the markdown row went too
    assert md.startswith("# Strategy Journal")       # ...but the header survives


def test_deleting_a_cycle_keeps_other_weeks_entries(at_step7, now_ist):
    """Only the deleted cycle's rows go — earlier weeks are untouched."""
    from app.schemas.journal import JournalEntry
    journal_repository.append(JournalEntry(
        cycle_id="2026-07-10", date="2026-07-10", logged_at="2026-07-10T16:00:00+05:30",
        regime="GREEN", regime_detail="prior week"))

    _run(now_ist, stops_verified=True)
    cycle_service.advance_part_e_journal(now=now_ist)
    _, removed = cycle_service.delete_current_cycle()

    assert removed == 1
    kept = journal_repository.read_entries()
    assert [e.cycle_id for e in kept] == ["2026-07-10"]
    assert "2026-07-10" in config.JOURNAL_MD.read_text(encoding="utf-8")


def test_stops_unverified_is_flagged(at_step7, now_ist):
    out = _run(now_ist).steps.part_e_journal.output
    assert out.entry.stops_verified is None
    assert any("stops" in a.lower() for a in out.actions)


# --- RED week ---------------------------------------------------------------

def test_red_regime_routes_to_journal_not_past_it(isolated, monkeypatch, now_ist):
    """Spec v2.2 §2.3: RED skips scan/rank/veto but still writes the journal."""
    _seed_holdings(HOLDINGS)
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: red_data())
    cycle_service.create_cycle(now=now_ist)
    regime_gate_service.run(now_ist=now_ist)
    cyc = cycle_service.advance_part_a(now=now_ist)

    assert cyc.outcome == "red_terminated"
    assert cyc.current_step == "part_e_journal"            # NOT skipped past
    assert cyc.steps.part_e_journal.status == StepStatus.PENDING
    assert cyc.steps.part_b_scanner.status == StepStatus.SKIPPED
    assert cyc.steps.part_d_risk_caps.status == StepStatus.SKIPPED


def test_red_week_entry_records_the_liquidation(isolated, journal_paths, monkeypatch, now_ist):
    _seed_holdings(HOLDINGS)
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: red_data())
    cycle_service.create_cycle(now=now_ist)
    regime_gate_service.run(now_ist=now_ist)
    cycle_service.advance_part_a(now=now_ist)

    out = _run(now_ist, stops_verified=True).steps.part_e_journal.output
    e = out.entry

    assert e.regime == "RED"
    assert len(e.exits) == len(HOLDINGS)          # everything marked for liquidation
    assert all(h.action == "EXIT" and h.reason == "regime RED" for h in e.holdings)
    assert e.buys == [] and e.strikeouts == []    # no scanning happened

    cyc = cycle_service.advance_part_e_journal(now=now_ist)
    assert cyc.current_step == "phase3_plan"
    assert len(journal_repository.read_entries()) == 1
