"""Step-8 Monday execution plan tests (Spec v2.3 §3.1-3.5)."""

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
    # v2.3: RED proceeds are parked, never left idle.
    assert out.park_amount == pytest.approx(len(HOLDINGS) * 50 * 100.0)
    assert out.redeem_amount is None
    assert out.parking_instrument == config.PARKING_INSTRUMENT
    text = "\n".join(out.checklist)
    assert "PARK the proceeds (~Rs 25,000)" in text and "liquid or overnight fund" in text
    assert text.index("LIQUIDATIONS FIRST") < text.index("PARK the proceeds")
    assert "slot short" not in text and "stays in cash" not in text

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
    """A slot-short remainder is parked rather than becoming an undersized buy."""
    out = _out(now_ist, portfolio_capital=1_000_000, available_cash=150_000)
    # 1 position funded (1 lakh), 50k left over — a slot short of the next.
    assert out.leftover_cash and out.leftover_cash > 0
    assert out.park_amount == out.leftover_cash == pytest.approx(50_000)
    assert any("slot short" in c and "liquid or overnight fund" in c for c in out.checklist)


# --- Spec v2.3: parking undeployed cash in a liquid fund ---------------------

def _red_cycle(monkeypatch, now_ist, holdings=HOLDINGS, qty=50):
    """Drive a RED week to the Monday plan."""
    _seed_holdings(holdings)
    if holdings:
        _edit(qty=qty)
    monkeypatch.setattr(regime_gate_service, "fetch_weekly_index_data", lambda *a, **k: red_data())
    cycle_service.create_cycle(now=now_ist)
    regime_gate_service.run(now_ist=now_ist)
    cycle_service.advance_part_a(now=now_ist)
    journal_service.run(JournalRunRequest(stops_verified=True), now_ist=now_ist)
    cycle_service.advance_part_e_journal(now=now_ist)
    monkeypatch.setattr(phase3_service.stock_prices, "get_prices",
                        lambda tickers, as_of=None, today=None: {t: _flat(100.0) for t in tickers})


def test_red_week_with_nothing_to_sell_keeps_cash_parked(isolated, journal_paths,  # noqa: F811
                                                         monkeypatch, now_ist):
    _red_cycle(monkeypatch, now_ist, holdings=[])
    out = _out(now_ist)

    assert out.exits == [] and out.park_amount is None
    text = "\n".join(out.checklist)
    assert "No exits this week" in text
    assert "No buys on a RED week — your cash stays parked in your liquid or overnight fund" in text


def test_green_buys_beyond_the_exit_proceeds_are_redeemed_first(at_step8, now_ist):
    """Nothing is being sold, so every rupee of the buys has to come back out
    of the liquid fund — and that instruction must be the FIRST line."""
    out = _out(now_ist, portfolio_capital=1_000_000, available_cash=250_000)

    assert out.est_proceeds is None
    assert out.redeem_amount == pytest.approx(out.est_deployment) == pytest.approx(200_000)
    assert out.checklist[0].startswith("BEFORE THE OPEN — have Rs 200,000 back")
    assert "liquid or overnight fund" in out.checklist[0]


def test_exit_proceeds_reduce_what_must_be_redeemed(at_step8, now_ist):
    _edit(0, status="PENDING_EXIT", exit_reason="rank_exit", qty=1500)   # frees 150,000
    out = _out(now_ist, portfolio_capital=1_000_000, available_cash=250_000)

    assert out.est_proceeds == pytest.approx(150_000)
    assert out.redeem_amount == pytest.approx(out.est_deployment - 150_000)
    assert out.checklist[0].startswith("BEFORE THE OPEN")


def test_no_redemption_when_the_exits_cover_the_buys(at_step8, now_ist):
    _edit(0, status="PENDING_EXIT", exit_reason="rank_exit", qty=5000)   # frees 500,000
    out = _out(now_ist, portfolio_capital=1_000_000, available_cash=250_000)

    assert out.redeem_amount is None
    assert not any("BEFORE THE OPEN" in c for c in out.checklist)
    assert out.checklist[0].startswith("09:15 — LIQUIDATIONS FIRST")


def test_no_redemption_when_nothing_is_funded(at_step8, now_ist):
    out = _out(now_ist, portfolio_capital=1_000_000, available_cash=40_000)

    assert out.funded_count == 0 and out.redeem_amount is None
    text = "\n".join(out.checklist)
    assert "No buys fit your available cash" in text
    assert "slot short" in text                      # the 40,000 is still parked
    assert out.park_amount == pytest.approx(40_000)


def _clear_buys(now_ist):
    """Empty the buy list on the committed risk-caps step: a GREEN week in which
    no candidate survived the veto ladder."""
    from app.repositories import cycles_repository as repo
    cyc = cycle_service.get_current_cycle()
    cyc.steps.part_d_risk_caps.output.buys = []
    repo.save(cyc)


def test_green_week_with_no_candidates_parks_free_cash(at_step8, now_ist):
    _clear_buys(now_ist)
    out = _out(now_ist, portfolio_capital=1_000_000, available_cash=300_000)

    assert out.buys == [] and out.park_amount == pytest.approx(300_000)
    text = "\n".join(out.checklist)
    assert "No buys this week — park the Rs 300,000 you have free" in text
    assert "slot short" not in text                  # nothing was a slot short


def test_green_week_with_no_candidates_and_no_free_cash(at_step8, now_ist):
    _clear_buys(now_ist)
    out = _out(now_ist, portfolio_capital=1_000_000, available_cash=0)

    assert out.park_amount is None
    assert any("No buys this week — your cash stays parked" in c for c in out.checklist)


def test_plan_states_the_earliest_promotion_date(at_step8, now_ist):
    out = _out(now_ist, portfolio_capital=1_000_000)
    assert any("not before 29 Jan 2027" in a for a in out.actions)


# --- paths the original suite never reached ----------------------------------

def test_a_share_dearer_than_the_position_size_is_deferred(at_step8, monkeypatch, now_ist):
    monkeypatch.setattr(phase3_service.stock_prices, "get_prices",
                        lambda tickers, as_of=None, today=None: {t: _flat(200_000.0)
                                                                 for t in tickers})
    out = _out(now_ist, portfolio_capital=1_000_000, available_cash=1_000_000)

    assert out.funded_count == 0
    assert all(b.defer_reason == "one share exceeds the target size" for b in out.buys)


def test_a_plan_failure_lands_on_the_step(at_step8, monkeypatch, now_ist):
    def boom(cycle, body):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(phase3_service, "_build", boom)
    step = phase3_service.run(Phase3RunRequest(), now_ist=now_ist).steps.phase3_plan

    assert step.status == StepStatus.FAILED and step.output is None
    assert step.error == "RuntimeError: ledger unreadable"


def test_the_plan_needs_a_regime_decision(at_step8):
    cyc = cycle_service.get_current_cycle()
    cyc.steps.part_a_regime_gate.output = None
    with pytest.raises(StepNotReady, match="regime gate"):
        phase3_service._build(cyc, Phase3RunRequest())
