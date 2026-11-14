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


# --- cash-aware buy funding (the real-Monday bug) ---------------------------

def test_buys_are_funded_in_rank_order_up_to_available_cash(at_step8, now_ist):
    """The bug from the first live Monday: the plan listed 5 buys needing far
    more than the cash a single exit freed. Buys must fund best-rank-first up to
    what is actually available, and defer the rest."""
    out = _out(now_ist, portfolio_capital=1_000_000, available_cash=250_000)

    funded = [b for b in out.buys if b.funded]
    deferred = [b for b in out.buys if not b.funded]
    # 10% of 10 lakh = 1 lakh each, so 2.5 lakh funds exactly 2 full positions.
    assert len(funded) == 2
    assert out.funded_count == 2 and out.deferred_count == len(deferred)
    # Funded ones are the best ranks; deferred are the worse ones.
    assert max(b.rank for b in funded) <= min((b.rank for b in deferred), default=999)
    assert all(b.defer_reason == "insufficient cash" for b in deferred)
    assert out.deployable <= 250_000


def test_deferred_buys_are_warned_and_listed(at_step8, now_ist):
    out = _out(now_ist, portfolio_capital=1_000_000, available_cash=150_000)
    assert out.deferred_count >= 1
    assert any("Deferred for want of cash" in w for w in out.warnings)
    text = "\n".join(out.checklist)
    assert "DEFERRED" in text


def test_available_cash_defaults_to_capital_minus_kept(at_step8, now_ist):
    """With no cash entered, everything you are not keeping is deployable —
    exiting positions plus any idle cash beyond your held value."""
    out = _out(now_ist, portfolio_capital=1_000_000)
    assert out.cash_source == "capital minus kept holdings"
    # capital 10L, kept holdings are tiny in the fixture, so nearly all is free.
    assert out.available_cash > 900_000


def test_entered_cash_overrides_the_exit_estimate(at_step8, now_ist):
    out = _out(now_ist, portfolio_capital=1_000_000, available_cash=90_000)
    assert out.cash_source == "entered"
    assert out.available_cash == 90_000


def test_cash_below_one_position_funds_nothing_and_says_so(at_step8, now_ist):
    out = _out(now_ist, portfolio_capital=1_000_000, available_cash=40_000)
    # A 10% position is 1 lakh; 40k funds none.
    assert out.funded_count == 0
    assert any("below one 10% position" in w for w in out.warnings)


def test_overweight_holdings_are_flagged(at_step8, monkeypatch, now_ist):
    """A holding well above 10% crowds out new names — the plan should say why
    there is less cash than open slots."""
    # SANSERA at 25 x ~100 (fixture prices everything at 100) is tiny; make one
    # holding large by entering a big quantity.
    _edit(0, qty=100000)                       # a deliberately huge position
    out = _out(now_ist, portfolio_capital=1_000_000, available_cash=500_000)
    assert any("Above the 10% target" in w for w in out.warnings)


def test_leftover_cash_is_reported_not_forced(at_step8, now_ist):
    """A slot-short remainder stays in cash rather than becoming an undersized buy."""
    out = _out(now_ist, portfolio_capital=1_000_000, available_cash=150_000)
    # 1 position funded (1 lakh), 50k left over — a slot short of the next.
    assert out.leftover_cash and out.leftover_cash > 0
    assert any("stays in cash" in c for c in out.checklist)
