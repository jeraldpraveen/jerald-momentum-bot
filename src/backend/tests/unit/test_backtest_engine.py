"""Backtest engine: execution mechanics, the weekly loop, and metrics.

The sanity checks here are the ones that catch a backtest lying to itself —
gap-down stop fills, costs actually reducing returns, RED weeks going to cash,
and the engine calling the same strategy core the live app uses.
"""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app import config as app_config
from app.backtest import config as bt_config
from app.backtest import engine, metrics, price_store, universe
from app.backtest.costs import CostModel
from app.backtest.portfolio import Holding, Portfolio, Trade
from tests.unit.test_backtest_data import bt_dirs  # noqa: F401


# --- costs ------------------------------------------------------------------

def test_slippage_is_always_adverse():
    c = CostModel(cost_bps=0, slippage_bps=100)     # 1%
    assert c.fill_price(100.0, "buy") == pytest.approx(101.0)
    assert c.fill_price(100.0, "sell") == pytest.approx(99.0)


def test_charges_are_a_cost_on_both_sides():
    c = CostModel(cost_bps=30, slippage_bps=0)
    assert c.charges(100_000) == pytest.approx(300.0)
    assert c.charges(-100_000) == pytest.approx(300.0)


# --- portfolio --------------------------------------------------------------

def _pf(cash=1_000_000.0, **kw):
    return Portfolio(cash=cash, costs=CostModel(**kw))


def test_buy_sets_the_stop_at_fill_times_multiplier():
    pf = _pf(cost_bps=0, slippage_bps=0)
    pf.buy("AAA", 100.0, budget=100_000, on=dt.date(2024, 1, 1), stop_multiplier=0.80)

    h = pf.holdings["AAA"]
    assert h.qty == 1000 and h.entry_price == pytest.approx(100.0)
    assert h.stop_price == pytest.approx(80.0)
    assert pf.cash == pytest.approx(900_000)


def test_buy_cannot_overdraw_the_account():
    pf = _pf(cash=1000.0, cost_bps=30, slippage_bps=25)
    pf.buy("AAA", 100.0, budget=1_000_000, on=dt.date(2024, 1, 1), stop_multiplier=0.8)
    assert pf.cash >= 0


def test_buy_returns_none_when_it_cannot_afford_one_share():
    pf = _pf(cash=50.0)
    assert pf.buy("AAA", 100.0, budget=50, on=dt.date(2024, 1, 1), stop_multiplier=0.8) is None
    assert pf.holdings == {}


def test_sell_closes_the_whole_position():
    pf = _pf(cost_bps=0, slippage_bps=0)
    pf.buy("AAA", 100.0, budget=100_000, on=dt.date(2024, 1, 1), stop_multiplier=0.8)
    pf.sell("AAA", 120.0, dt.date(2024, 2, 1), reason="rank_exit")

    assert pf.holdings == {}
    assert pf.cash == pytest.approx(1_020_000)      # +20% on a tenth of capital
    assert pf.trades[-1].reason == "rank_exit"


def test_unpriced_holding_is_valued_at_cost_not_zero():
    """A missing quote means unknown, never worthless — otherwise equity craters
    the moment a ticker goes quiet."""
    pf = _pf(cost_bps=0, slippage_bps=0)
    pf.buy("AAA", 100.0, budget=100_000, on=dt.date(2024, 1, 1), stop_multiplier=0.8)
    assert pf.equity({}) == pytest.approx(1_000_000)


def test_liquidate_sells_everything_priceable():
    pf = _pf(cost_bps=0, slippage_bps=0)
    for s in ("AAA", "BBB"):
        pf.buy(s, 100.0, budget=100_000, on=dt.date(2024, 1, 1), stop_multiplier=0.8)

    pf.liquidate({"AAA": 110.0, "BBB": 90.0}, dt.date(2024, 2, 1), reason="regime_red")
    assert pf.holdings == {}
    assert all(t.reason == "regime_red" for t in pf.trades if t.side == "sell")


# --- metrics ----------------------------------------------------------------

def _curve(values, start="2024-01-05"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="W-FRI"))


def test_metrics_on_a_flat_curve_are_zero():
    m = metrics.compute(_curve([100.0] * 20))
    assert m.total_return_pct == 0.0 and m.sharpe == 0.0 and m.max_drawdown_pct == 0.0


def test_doubling_in_a_year_is_about_100_pct_cagr():
    m = metrics.compute(_curve(np.linspace(100, 200, 53)))
    assert m.total_return_pct == pytest.approx(100.0, abs=0.1)
    assert m.cagr_pct == pytest.approx(100.0, abs=5.0)


def test_max_drawdown_measures_peak_to_trough():
    m = metrics.compute(_curve([100, 120, 60, 80, 130]))
    assert m.max_drawdown_pct == pytest.approx(-50.0)     # 120 -> 60


def test_exposure_counts_weeks_holding_something():
    m = metrics.compute(_curve([100, 101, 102, 103]), exposure=[True, True, False, False])
    assert m.exposure_pct == 50.0


def test_metrics_survive_a_single_point_curve():
    m = metrics.compute(_curve([100.0]))
    assert m.weeks == 1 and m.cagr_pct == 0.0


def test_trade_stats_counts_round_trips():
    trades = [
        Trade(dt.date(2024, 1, 1), "AAA", "buy", 10, 100, 1000, 0),
        Trade(dt.date(2024, 2, 1), "AAA", "sell", 10, 120, 1200, 0),
        Trade(dt.date(2024, 1, 1), "BBB", "buy", 10, 100, 1000, 0),
        Trade(dt.date(2024, 2, 1), "BBB", "sell", 10, 80, 800, 0),
    ]
    s = metrics.trade_stats(trades)
    assert s["closed_trades"] == 2 and s["win_rate_pct"] == 50.0
    assert s["best_pnl"] == 200.0 and s["worst_pnl"] == -200.0


# --- the weekly loop --------------------------------------------------------

def _series(n, start="2020-01-01", drift=0.0015, seed=0, vol=0.012):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, vol, n))
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame(
        {"open": close, "high": close * 1.02, "low": close * 0.98,
         "close": close, "volume": 5_000_000.0},
        index=idx,
    )


@pytest.fixture
def market(bt_dirs):  # noqa: F811
    """A small investable world: an index plus six names, all with long history."""
    index_ticker = app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker
    price_store.save(index_ticker, _series(900, seed=1))

    members = []
    for i in range(6):
        sym = f"S{i}"
        price_store.save(f"{sym}.NS", _series(900, drift=0.002 - i * 0.0004, seed=10 + i))
        members.append(universe.UniverseMember(sym, f"Sector{i % 3}", "MIDSMALLCAP400"))
    universe.save("testu", members)
    return index_ticker


def _run(**kw):
    params = dict(universe_id="testu", starting_capital=1_000_000.0)
    params.update(kw)
    return engine.run_backtest(**params)


def test_run_produces_a_curve_and_metrics(market):
    res = _run()
    assert len(res["equity_curve"]) > 10
    assert res["metrics"]["weeks"] == len(res["equity_curve"])
    assert res["config"]["universe_size"] == 6
    assert res["benchmark_curve"]                       # buy & hold comparison


def test_equity_starts_at_the_starting_capital(market):
    res = _run(start=dt.date(2022, 1, 1))
    first = res["equity_curve"][0]["equity"]
    assert first == pytest.approx(1_000_000, rel=0.35)  # first week is partly deployed


def test_costs_reduce_returns_monotonically(market):
    """A strategy that only works at zero cost is not a strategy."""
    free = _run(cost_bps=0, slippage_bps=0)["metrics"]["end_equity"]
    cheap = _run(cost_bps=10, slippage_bps=10)["metrics"]["end_equity"]
    dear = _run(cost_bps=100, slippage_bps=100)["metrics"]["end_equity"]
    assert free >= cheap >= dear


def test_red_regime_goes_to_cash(market, monkeypatch):
    monkeypatch.setattr(engine.strategy, "decide_regime",
                        lambda weekly, filt: ("RED", "Supertrend", 1.0))
    res = _run()

    assert all(w["regime"] == "RED" for w in res["weeks"])
    assert all(w["buys"] == [] for w in res["weeks"])
    assert res["equity_curve"][-1]["positions"] == 0
    # Never invested, so equity is flat at the starting capital.
    assert res["metrics"]["end_equity"] == pytest.approx(1_000_000, rel=0.01)


def test_always_green_filter_deploys_capital(market):
    res = _run(filter_id="always_green")
    assert any(w["buys"] for w in res["weeks"])
    assert any(row["positions"] > 0 for row in res["equity_curve"])


def test_position_count_respects_the_portfolio_cap(market):
    res = _run(filter_id="always_green")
    assert max(row["positions"] for row in res["equity_curve"]) <= app_config.TARGET_PORTFOLIO_MAX


def test_stop_fills_at_the_gap_not_the_stop(bt_dirs):  # noqa: F811
    """A gap-down must fill at the open, below the stop — the optimistic
    alternative is how a backtest invents money it never had."""
    pf = Portfolio(cash=0.0, costs=CostModel(cost_bps=0, slippage_bps=0))
    pf.holdings["AAA"] = Holding(
        symbol="AAA", qty=100, entry_price=100.0, entry_date=dt.date(2024, 1, 1),
        stop_price=80.0)

    idx = pd.bdate_range("2024-01-01", periods=5)
    gap = pd.DataFrame({"open": [100, 100, 70, 75, 75], "high": [101] * 5,
                        "low": [99, 99, 68, 74, 74], "close": [100, 100, 70, 75, 75],
                        "volume": [1e6] * 5}, index=idx)
    price_store.save("AAA.NS", gap)

    out = engine._apply_stops(pf, ["AAA.NS"], idx[0].date(), idx[-1].date(), 0.0)

    assert out["symbols"] == ["AAA"]
    assert pf.trades[-1].price == pytest.approx(70.0)   # the gap, not 80
    assert pf.holdings == {}


def test_empty_universe_is_a_clear_error(bt_dirs):  # noqa: F811
    universe.save("empty", [])
    with pytest.raises(engine.BacktestError, match="empty"):
        engine.run_backtest(universe_id="empty")


def test_missing_index_history_is_a_clear_error(bt_dirs):  # noqa: F811
    universe.save("u", [universe.UniverseMember("AAA")])
    with pytest.raises(engine.BacktestError, match="No cached history"):
        engine.run_backtest(universe_id="u")


def test_impossible_date_range_is_a_clear_error(market):
    with pytest.raises(engine.BacktestError, match="No tradable weeks"):
        _run(start=dt.date(2099, 1, 1))


def test_limitations_are_always_reported(market):
    res = _run()
    text = " ".join(res["limitations"]).lower()
    assert "survivorship" in text
    assert "fundamental veto" in text and "not" in text


def test_engine_uses_the_shared_strategy_core(market, monkeypatch):
    """If the engine ever forks the rules, this fails — that fork is the whole
    risk the pure core exists to prevent."""
    seen = {"caps": 0, "audit": 0, "technical": 0}
    real_caps = engine.strategy.apply_caps

    monkeypatch.setattr(engine.strategy, "apply_caps",
                        lambda *a, **k: (seen.__setitem__("caps", seen["caps"] + 1),
                                         real_caps(*a, **k))[1])
    real_audit = engine.strategy.audit_holdings
    monkeypatch.setattr(engine.strategy, "audit_holdings",
                        lambda *a, **k: (seen.__setitem__("audit", seen["audit"] + 1),
                                         real_audit(*a, **k))[1])
    real_tech = engine.strategy.evaluate_technical
    monkeypatch.setattr(engine.strategy, "evaluate_technical",
                        lambda *a, **k: (seen.__setitem__("technical", seen["technical"] + 1),
                                         real_tech(*a, **k))[1])

    _run(filter_id="always_green")
    assert all(v > 0 for v in seen.values()), seen


# --- RED-regime alt asset (park instead of idle cash) -----------------------

def test_alt_asset_is_bought_on_red_and_sold_on_green(bt_dirs, monkeypatch):  # noqa: F811
    """The parking asset must be held only while the regime is RED."""
    from app import config as app_config
    index_ticker = app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker
    price_store.save(index_ticker, _series(900, seed=1))
    price_store.save("GOLDBEES.NS", _series(900, drift=0.0008, seed=99))
    price_store.save("S0.NS", _series(900, drift=0.002, seed=10))
    universe.save("u", [universe.UniverseMember("S0", "Auto", "MIDSMALLCAP400")])

    flips = {"n": 0}

    def alternating(weekly, filt):
        flips["n"] += 1
        return ("RED" if (flips["n"] // 20) % 2 else "GREEN", "Supertrend", 1.0)

    monkeypatch.setattr(engine.strategy, "decide_regime", alternating)
    res = engine.run_backtest(universe_id="u", alt_asset_id="gold",
                              starting_capital=1_000_000.0)

    parks = [t for t in res["trades"] if t["reason"] == "park_alt"]
    unparks = [t for t in res["trades"] if t["reason"] == "unpark_alt"]
    assert parks and unparks, "gold should have been parked and unparked"
    assert all(t["symbol"] == "GOLDBEES.NS" for t in parks + unparks)
    # Every RED stretch parks; every GREEN stretch releases it for equities.
    assert abs(len(parks) - len(unparks)) <= 1

    red_rows = [r for r in res["equity_curve"] if r["regime"] == "RED"]
    assert any(r["alt_value"] > 0 for r in red_rows)
    assert all(r["alt_value"] == 0 for r in res["equity_curve"] if r["regime"] == "GREEN")


def test_alt_asset_never_reaches_the_strategy_core(bt_dirs, monkeypatch):  # noqa: F811
    """Gold must never be ranked, rank-exited or stopped out like an equity."""
    from app import config as app_config
    price_store.save(app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker,
                     _series(900, seed=1))
    price_store.save("GOLDBEES.NS", _series(900, seed=99))
    price_store.save("S0.NS", _series(900, drift=0.002, seed=10))
    universe.save("u", [universe.UniverseMember("S0", "Auto", "MIDSMALLCAP400")])

    monkeypatch.setattr(engine.strategy, "decide_regime",
                        lambda w, f: ("RED", "Supertrend", 1.0))
    res = engine.run_backtest(universe_id="u", alt_asset_id="gold")

    for w in res["weeks"]:
        assert "GOLDBEES.NS" not in w["held"]
        assert "GOLDBEES.NS" not in w["buys"] and "GOLDBEES.NS" not in w["exits"]
        assert "GOLDBEES.NS" not in w["stops_hit"]


def test_partial_allocation_leaves_the_rest_in_cash(bt_dirs, monkeypatch):  # noqa: F811
    from app import config as app_config
    price_store.save(app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker,
                     _series(900, seed=1))
    price_store.save("GOLDBEES.NS", _series(900, seed=99))
    price_store.save("S0.NS", _series(900, seed=10))
    universe.save("u", [universe.UniverseMember("S0", "Auto", "MIDSMALLCAP400")])
    monkeypatch.setattr(engine.strategy, "decide_regime",
                        lambda w, f: ("RED", "Supertrend", 1.0))

    res = engine.run_backtest(universe_id="u", alt_asset_id="gold",
                              alt_allocation_pct=40.0, starting_capital=1_000_000.0)
    row = next(r for r in res["equity_curve"] if r["alt_value"] > 0)
    assert row["alt_value"] / row["equity"] == pytest.approx(0.40, abs=0.03)
    assert row["cash"] > 0                       # the other 60% stays in cash


def test_no_alt_asset_keeps_the_rulebook_behaviour(bt_dirs, monkeypatch):  # noqa: F811
    """Default is cash — the live rule — and must be unchanged."""
    from app import config as app_config
    price_store.save(app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker,
                     _series(900, seed=1))
    price_store.save("S0.NS", _series(900, seed=10))
    universe.save("u", [universe.UniverseMember("S0", "Auto", "MIDSMALLCAP400")])
    monkeypatch.setattr(engine.strategy, "decide_regime",
                        lambda w, f: ("RED", "Supertrend", 1.0))

    res = engine.run_backtest(universe_id="u")
    assert all(r["alt_value"] == 0 for r in res["equity_curve"])
    assert not [t for t in res["trades"] if t["reason"] in ("park_alt", "unpark_alt")]
    assert res["config"]["alt_asset_id"] is None


def test_alt_asset_limitation_is_reported(bt_dirs, monkeypatch):  # noqa: F811
    from app import config as app_config
    price_store.save(app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker,
                     _series(900, seed=1))
    price_store.save("GOLDBEES.NS", _series(900, seed=99))
    price_store.save("S0.NS", _series(900, seed=10))
    universe.save("u", [universe.UniverseMember("S0", "Auto", "MIDSMALLCAP400")])
    monkeypatch.setattr(engine.strategy, "decide_regime",
                        lambda w, f: ("RED", "Supertrend", 1.0))

    res = engine.run_backtest(universe_id="u", alt_asset_id="gold")
    assert any("GOLDBEES" in l and "2009" in l for l in res["limitations"])


def test_a_clipped_start_date_is_reported_not_silent(bt_dirs):  # noqa: F811
    """A 2010 start against a 2024 index quietly became a 2025 run — the run must
    say so, or the user reads results for a window they never asked for."""
    from app import config as app_config
    idx = app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker
    price_store.save(idx, _series(600, start="2023-01-02", seed=1))
    price_store.save("S0.NS", _series(900, start="2020-01-01", seed=10))
    universe.save("u", [universe.UniverseMember("S0", "Auto", "MIDSMALLCAP400")])

    res = engine.run_backtest(universe_id="u", start=dt.date(2010, 1, 1),
                              filter_id="always_green")

    assert res["config"]["requested_start"] == "2010-01-01"
    assert res["config"]["start"] > "2010-01-01"
    assert res["config"]["start_clipped"] is True
    assert any("2010-01-01" in l and "warm-up" in l for l in res["limitations"])


def test_an_honoured_start_date_is_not_flagged(bt_dirs):  # noqa: F811
    from app import config as app_config
    idx = app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker
    price_store.save(idx, _series(900, start="2020-01-01", seed=1))
    price_store.save("S0.NS", _series(900, start="2020-01-01", seed=10))
    universe.save("u", [universe.UniverseMember("S0", "Auto", "MIDSMALLCAP400")])

    res = engine.run_backtest(universe_id="u", start=dt.date(2022, 6, 1),
                              filter_id="always_green")
    assert res["config"]["start_clipped"] is False
    assert not any("warm-up" in l for l in res["limitations"])


def test_win_rate_separates_cash_weeks_from_losing_weeks():
    """Sitting in cash is not losing. Counting flat weeks as non-wins made a
    58%-invested strategy look like it won only a third of its weeks."""
    # weekly moves: +10%, flat(cash), +10%, flat(cash), -5%
    eq = _curve([100, 110, 110, 121, 121, 115])
    m = metrics.compute(eq, exposure=[True, True, False, True, False, True])

    assert m.win_rate_pct == 40.0                 # 2 of 5 moves > 0; flat weeks drag
    assert m.win_rate_invested_pct == pytest.approx(66.67, abs=0.1)
    assert m.exposure_pct == pytest.approx(66.67, abs=0.1)


def test_invested_win_rate_is_zero_without_exposure_data():
    m = metrics.compute(_curve([100, 110, 120]))
    assert m.win_rate_invested_pct == 0.0


def test_cash_yield_compounds_only_on_idle_cash(bt_dirs, monkeypatch):  # noqa: F811
    """A fixed yield on cash is the honest way to model a bond/liquid parking
    baseline — no ETF, no dividend-capture artifact, and it covers the full
    window. It must lift a mostly-cash (RED) run and leave a fully-invested
    run essentially unchanged."""
    from app import config as app_config
    idx = app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker
    price_store.save(idx, _series(900, seed=1))
    price_store.save("S0.NS", _series(900, drift=0.002, seed=10))
    universe.save("u", [universe.UniverseMember("S0", "Auto", "MIDSMALLCAP400")])

    monkeypatch.setattr(engine.strategy, "decide_regime",
                        lambda w, f: ("RED", "Supertrend", 1.0))   # always cash
    flat = engine.run_backtest(universe_id="u", cash_yield_pct=0.0)
    paid = engine.run_backtest(universe_id="u", cash_yield_pct=7.0)

    assert paid["metrics"]["end_equity"] > flat["metrics"]["end_equity"]
    # ~7%/yr over the simulated span, give or take the warm-up.
    yrs = flat["metrics"]["weeks"] / 52
    assert paid["metrics"]["end_equity"] / flat["metrics"]["end_equity"] == \
        pytest.approx(1.07 ** yrs, rel=0.05)
    assert paid["config"]["cash_yield_pct"] == 7.0


def test_cash_yield_default_is_zero(bt_dirs, monkeypatch):  # noqa: F811
    from app import config as app_config
    price_store.save(app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker,
                     _series(900, seed=1))
    price_store.save("S0.NS", _series(900, seed=10))
    universe.save("u", [universe.UniverseMember("S0", "Auto", "MIDSMALLCAP400")])
    res = engine.run_backtest(universe_id="u")
    assert res["config"]["cash_yield_pct"] == 0.0
