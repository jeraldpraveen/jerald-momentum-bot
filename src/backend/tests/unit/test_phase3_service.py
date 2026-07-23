"""Step-8 Monday execution plan tests (Spec v2.2 §3.1-3.4)."""

import numpy as np
import pytest

from app import config
from app.schemas.common import StepStatus
from app.schemas.fundamental import PartDFundamentalRunRequest
from app.schemas.journal import JournalRunRequest
from app.schemas.phase3 import Phase3RunRequest
from app.services import (
    cycle_service,
    fundamental_service,
    journal_service,
    phase3_service,
    regime_gate_service,
    risk_caps_service,
    technical_service,
)
from app.repositories import ledger_repository as ledger
from app.services.errors import StepNotReady
from tests.conftest import red_data
from tests.unit.test_fundamental_service import at_step4  # noqa: F401
from tests.unit.test_journal_service import journal_paths  # noqa: F401
from tests.unit.test_scanner_service import HOLDINGS, _ohlcv, _seed_holdings


def _flat(price: float, n: int = 300):
    return _ohlcv(np.full(n, price))


@pytest.fixture
def at_step8(at_step4, journal_paths, monkeypatch, now_ist):  # noqa: F811
    """Drive a GREEN cycle through Steps 4-7 to the Monday plan."""
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

    journal_service.run(JournalRunRequest(stops_verified=True), now_ist=now_ist)
    cycle_service.advance_part_e_journal(now=now_ist)

    # Everything trades at a round 100 so quantities are easy to reason about.
    monkeypatch.setattr(phase3_service.stock_prices, "get_prices",
                        lambda tickers, as_of=None, today=None: {t: _flat(100.0) for t in tickers})
    _edit(qty=50)          # the shared fixture seeds no quantities
    return journal_paths


def _edit(row: int | None = None, **values):
    """Patch the ledger through the repository so dtypes stay well-behaved."""
    df = ledger.load_ledger()
    target = df.index if row is None else [df.index[row]]
    for col, val in values.items():
        df.loc[target, col] = val
    ledger.save_ledger(df)


def _out(now_ist, **kw):
    return phase3_service.run(Phase3RunRequest(**kw), now_ist=now_ist).steps.phase3_plan.output


def test_requires_step8(at_step4, now_ist):  # noqa: F811
    with pytest.raises(StepNotReady):
        _out(now_ist)


def test_buys_sized_to_ten_percent_of_capital(at_step8, now_ist):
    out = _out(now_ist, portfolio_capital=1_000_000)
    assert out.capital_source == "entered"
    for b in out.buys:
        assert b.target_value == pytest.approx(100_000)     # 10% of 10 lakh
        assert b.indicative_qty == 1000                     # 100,000 / 100
        assert b.indicative_stop == pytest.approx(80.0)     # 100 x 0.80


def test_capital_estimated_from_ledger_when_not_given(at_step8, now_ist):
    out = _out(now_ist)
    assert out.capital_source == "estimated from ledger"
    assert any("estimated" in w.lower() for w in out.warnings)


def test_checklist_puts_liquidations_before_buys(at_step8, monkeypatch, now_ist):
    # Mark a holding for exit so both sections appear.
    _edit(0, status="PENDING_EXIT", exit_reason="rank_exit")

    out = _out(now_ist, portfolio_capital=1_000_000)
    text = "\n".join(out.checklist)
    assert "LIQUIDATIONS FIRST" in text
    if out.buys:
        assert text.index("LIQUIDATIONS FIRST") < text.index("DEPLOY")
    assert "Never bulk-cancel" in text


def test_exit_instructions_cancel_that_gtt_then_sell(at_step8, now_ist):
    _edit(0, status="PENDING_EXIT", exit_reason="rank_exit")

    out = _out(now_ist)
    assert len(out.exits) == 1
    e = out.exits[0]
    assert e.existing_stop is not None          # the GTT to cancel
    line = next(l for l in out.checklist if e.symbol in l)
    assert "cancel its GTT" in line and "SELL" in line


def test_missing_qty_is_warned_not_guessed(at_step8, now_ist):
    _edit(0, status="PENDING_EXIT", exit_reason="rank_exit", qty=None)

    out = _out(now_ist)
    assert out.exits[0].qty is None
    assert any("no quantity" in w for w in out.warnings)


def test_grandfathering_note_present(at_step8, now_ist):
    out = _out(now_ist, portfolio_capital=1_000_000)
    assert any("grandfathered" in c.lower() for c in out.checklist)


def test_complete_marks_cycle_done(at_step8, now_ist):
    cyc = phase3_service.run(Phase3RunRequest(portfolio_capital=1_000_000), now_ist=now_ist)
    assert cyc.steps.phase3_plan.status == StepStatus.DONE

    done = cycle_service.complete_cycle(now=now_ist)
    assert done.current_step == "done"
    assert done.outcome == "green_completed"
    assert done.status == "open"        # stays readable until you close it


# --- RED week ---------------------------------------------------------------

def test_red_week_plan_is_exits_only(isolated, journal_paths, monkeypatch, now_ist):  # noqa: F811
    _seed_holdings(HOLDINGS)
    _edit(qty=50)
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: red_data())
    cycle_service.create_cycle(now=now_ist)
    regime_gate_service.run(now_ist=now_ist)
    cycle_service.advance_part_a(now=now_ist)          # -> journal
    journal_service.run(JournalRunRequest(stops_verified=True), now_ist=now_ist)
    cycle_service.advance_part_e_journal(now=now_ist)  # -> phase 3

    monkeypatch.setattr(phase3_service.stock_prices, "get_prices",
                        lambda tickers, as_of=None, today=None: {t: _flat(100.0) for t in tickers})
    out = _out(now_ist)

    assert out.regime == "RED"
    assert len(out.exits) == len(HOLDINGS)             # everything liquidated
    assert out.buys == []
    assert any("stays in cash" in c for c in out.checklist)

    done = cycle_service.complete_cycle(now=now_ist)
    assert done.outcome == "red_terminated"           # not overwritten
