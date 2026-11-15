"""Spec v2.3 backtest additions: tax in the books, a fair benchmark, parked cash.

These are the checks behind "does the strategy beat simply holding the index
fund?" — the question a pre-tax run against a price index cannot answer.
"""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app import config as app_config
from app.backtest import config as bt_config
from app.backtest import engine, metrics, price_store, tax, universe
from app.backtest.costs import CostModel
from app.backtest.portfolio import Holding, Portfolio, Trade
from app.backtest.tax import TaxLedger
from tests.unit.test_backtest_data import bt_dirs  # noqa: F401
from tests.unit.test_backtest_engine import _series, market  # noqa: F401

D0 = dt.date(2024, 5, 1)


def _pf(cash=1_000_000.0, taxed=True, cost_bps=0.0, slippage_bps=0.0):
    return Portfolio(cash=cash, costs=CostModel(cost_bps=cost_bps, slippage_bps=slippage_bps),
                     tax=TaxLedger() if taxed else None)


# --- portfolio: cost basis ---------------------------------------------------

def test_buy_records_the_cost_basis_including_charges():
    pf = _pf(cost_bps=100)                               # 1% charges
    pf.buy("AAA", 100.0, budget=10_000, on=D0, stop_multiplier=0.8)
    assert pf.holdings["AAA"].cost == pytest.approx(10_000 * 1.01)


def test_averaging_up_accumulates_cost_and_keeps_the_first_entry_date():
    pf = _pf(cost_bps=100)
    pf.buy("AAA", 100.0, budget=10_000, on=D0, stop_multiplier=0.8)
    pf.buy("AAA", 200.0, budget=20_000, on=D0 + dt.timedelta(days=30), stop_multiplier=0.8)

    h = pf.holdings["AAA"]
    assert h.qty == 200 and h.entry_date == D0
    assert h.entry_price == pytest.approx(150.0)
    assert h.stop_price == pytest.approx(120.0)
    assert h.cost == pytest.approx(30_000 * 1.01)


def test_basis_falls_back_to_entry_value_when_no_cost_was_recorded():
    h = Holding(symbol="AAA", qty=10, entry_price=50.0, entry_date=D0, stop_price=40.0)
    assert h.basis() == 500.0
    h.cost = 520.0
    assert h.basis() == 520.0


def test_buy_refuses_a_non_positive_price():
    pf = _pf()
    assert pf.buy("AAA", 0.0, budget=10_000, on=D0, stop_multiplier=0.8) is None


def test_buy_refuses_when_charges_push_one_share_out_of_reach():
    pf = _pf(cash=100.0, cost_bps=100)                   # 1 share = 100 + 1 charge
    assert pf.buy("AAA", 100.0, budget=100, on=D0, stop_multiplier=0.8) is None
    assert pf.cash == 100.0


def test_selling_a_name_that_is_not_held_is_a_no_op():
    assert _pf().sell("NOPE", 100.0, D0) is None


# --- portfolio: tax on sales --------------------------------------------------

def test_a_profitable_sale_pays_short_term_tax_out_of_cash():
    pf = _pf()
    pf.buy("AAA", 100.0, budget=100_000, on=D0, stop_multiplier=0.8)
    t = pf.sell("AAA", 150.0, D0 + dt.timedelta(days=60), reason="rank_exit")

    assert t.pnl == pytest.approx(50_000.0)
    assert t.tax == pytest.approx(10_000.0)              # 20% of the gain
    assert pf.cash == pytest.approx(1_000_000 + 50_000 - 10_000)


def test_a_later_loss_refunds_tax_paid_earlier_in_the_year():
    pf = _pf()
    pf.buy("AAA", 100.0, budget=100_000, on=D0, stop_multiplier=0.8)
    pf.sell("AAA", 150.0, D0 + dt.timedelta(days=30))
    pf.buy("BBB", 100.0, budget=100_000, on=D0 + dt.timedelta(days=31), stop_multiplier=0.8)
    t = pf.sell("BBB", 80.0, D0 + dt.timedelta(days=60))

    assert t.pnl == pytest.approx(-20_000.0)
    assert t.tax == pytest.approx(-4_000.0)              # refund
    assert pf.tax.capital_gains_tax == pytest.approx(6_000.0)


def test_a_position_held_over_a_year_is_taxed_as_long_term():
    pf = _pf()
    pf.buy("AAA", 100.0, budget=100_000, on=D0, stop_multiplier=0.8)
    t = pf.sell("AAA", 400.0, D0 + dt.timedelta(days=400))

    assert t.pnl == pytest.approx(300_000.0)
    assert t.tax == pytest.approx((300_000 - 125_000) * 0.125)


def test_sale_pnl_is_net_of_both_legs_charges():
    pf = _pf(cost_bps=100)
    pf.buy("AAA", 100.0, budget=10_000, on=D0, stop_multiplier=0.8)
    qty = pf.holdings["AAA"].qty
    t = pf.sell("AAA", 100.0, D0 + dt.timedelta(days=5))
    assert t.pnl == pytest.approx(-2 * qty * 100 * 0.01)   # flat price, two charges lost


def test_without_a_ledger_sales_record_pnl_but_pay_no_tax():
    pf = _pf(taxed=False)
    pf.buy("AAA", 100.0, budget=100_000, on=D0, stop_multiplier=0.8)
    t = pf.sell("AAA", 150.0, D0 + dt.timedelta(days=60))

    assert t.pnl == pytest.approx(50_000.0) and t.tax == 0.0
    assert pf.cash == pytest.approx(1_050_000)


def test_trades_default_to_no_pnl_and_no_tax():
    t = Trade(D0, "AAA", "buy", 1, 100.0, 100.0, 0.0)
    assert t.pnl is None and t.tax == 0.0


# --- portfolio: liquidation tax ----------------------------------------------

def test_liquidation_tax_is_zero_for_a_pre_tax_portfolio():
    pf = _pf(taxed=False)
    pf.buy("AAA", 100.0, budget=100_000, on=D0, stop_multiplier=0.8)
    assert pf.liquidation_tax({"AAA": 200.0}, D0 + dt.timedelta(days=30)) == 0.0


def test_liquidation_tax_prices_open_gains_without_touching_the_books():
    pf = _pf()
    pf.buy("AAA", 100.0, budget=100_000, on=D0, stop_multiplier=0.8)
    pf.buy("BBB", 100.0, budget=100_000, on=D0, stop_multiplier=0.8)
    cash = pf.cash

    owed = pf.liquidation_tax({"AAA": 150.0}, D0 + dt.timedelta(days=30))   # BBB unpriced

    assert owed == pytest.approx(10_000.0)               # only AAA's 50,000 gain
    assert pf.cash == cash and set(pf.holdings) == {"AAA", "BBB"}
    assert pf.tax.total_paid == 0.0                      # the real ledger is untouched


def test_liquidation_tax_includes_the_parked_asset_as_income():
    pf = _pf()
    pf.buy_alt("GOLD", 100.0, 100_000, D0)
    assert pf.liquidation_tax({"GOLD": 110.0}, D0 + dt.timedelta(days=30)) == \
        pytest.approx(10_000 * 0.30)
    assert pf.liquidation_tax({}, D0 + dt.timedelta(days=30)) == 0.0        # unpriced


# --- portfolio: the parking asset ---------------------------------------------

@pytest.mark.parametrize("price,budget", [(0.0, 1000.0), (100.0, 0.0), (100.0, 50.0)])
def test_buy_alt_refuses_what_it_cannot_buy(price, budget):
    pf = _pf()
    assert pf.buy_alt("GOLD", price, budget, D0) is None
    assert pf.alt is None


def test_buy_alt_drops_a_share_when_charges_would_overdraw():
    pf = _pf(cash=1_000.0, cost_bps=100)                 # 10 shares = 1000 + 10 charges
    t = pf.buy_alt("GOLD", 100.0, 1_000.0, D0)
    assert t.qty == 9 and pf.cash >= 0


def test_buy_alt_refuses_when_even_one_share_plus_charges_does_not_fit():
    pf = _pf(cash=100.0, cost_bps=100)
    assert pf.buy_alt("GOLD", 100.0, 100.0, D0) is None


def test_adding_to_the_parked_asset_averages_price_and_cost():
    pf = _pf(cost_bps=100)
    pf.buy_alt("GOLD", 100.0, 10_000, D0)
    pf.buy_alt("GOLD", 200.0, 20_000, D0 + dt.timedelta(days=7))

    assert pf.alt.qty == 200 and pf.alt.entry_price == pytest.approx(150.0)
    assert pf.alt.cost == pytest.approx(30_000 * 1.01)


def test_a_different_parking_asset_replaces_the_old_holding():
    pf = _pf()
    pf.buy_alt("GOLD", 100.0, 10_000, D0)
    pf.buy_alt("SILVER", 50.0, 10_000, D0)
    assert pf.alt.symbol == "SILVER" and pf.alt.qty == 200


def test_sell_alt_with_nothing_parked_is_a_no_op():
    assert _pf().sell_alt(100.0, D0) is None


def test_parked_asset_gain_is_taxed_as_income():
    pf = _pf()
    pf.buy_alt("GOLD", 100.0, 100_000, D0)
    t = pf.sell_alt(110.0, D0 + dt.timedelta(days=30))

    assert t.pnl == pytest.approx(10_000.0) and t.tax == pytest.approx(3_000.0)
    assert pf.cash == pytest.approx(1_000_000 + 10_000 - 3_000)


def test_parked_asset_loss_earns_no_refund():
    pf = _pf()
    pf.buy_alt("GOLD", 100.0, 100_000, D0)
    t = pf.sell_alt(90.0, D0 + dt.timedelta(days=30))
    assert t.pnl == pytest.approx(-10_000.0) and t.tax == 0.0


def test_parked_asset_sale_without_a_ledger_pays_no_tax():
    pf = _pf(taxed=False)
    pf.buy_alt("GOLD", 100.0, 100_000, D0)
    assert pf.sell_alt(110.0, D0 + dt.timedelta(days=30)).tax == 0.0


# --- metrics ------------------------------------------------------------------

def test_cagr_doubles_in_one_year():
    assert metrics.cagr_pct(100.0, 200.0, 52) == pytest.approx(100.0)


@pytest.mark.parametrize("start,end,periods", [(0.0, 100.0, 52), (100.0, 0.0, 52),
                                                (100.0, 200.0, 0)])
def test_cagr_is_zero_when_it_cannot_be_computed(start, end, periods):
    assert metrics.cagr_pct(start, end, periods) == 0.0


def test_buy_and_hold_of_an_empty_series_is_empty():
    assert metrics.buy_and_hold(pd.Series(dtype=float), 1000.0).empty


def test_turnover_is_zero_without_equity_or_weeks():
    assert metrics.turnover(1000.0, 0.0, 10) == 0.0
    assert metrics.turnover(1000.0, 1000.0, 0) == 0.0


def test_trade_stats_ignores_a_sell_with_no_matching_buy():
    s = metrics.trade_stats([Trade(D0, "AAA", "sell", 10, 100, 1000, 0)])
    assert s["closed_trades"] == 0 and s["avg_pnl"] == 0.0


# --- engine: tax --------------------------------------------------------------

def test_a_default_run_is_after_tax_and_says_so(market):  # noqa: F811
    res = engine.run_backtest(universe_id="testu", filter_id="always_green")

    t = res["tax"]
    assert res["config"]["apply_tax"] is True and t["enabled"] is True
    assert t["rules"]["stcg_pct"] == 20.0 and t["by_year"]
    assert t["total_tax_paid"] == pytest.approx(
        t["capital_gains_tax"] + t["cash_income_tax"], abs=0.02)
    assert any("TODAY'S rates" in l for l in res["limitations"])
    assert not any("NOT modelled" in l for l in res["limitations"])


def test_opting_out_of_tax_is_reported_as_a_limitation(market):  # noqa: F811
    res = engine.run_backtest(universe_id="testu", filter_id="always_green",
                              apply_tax=False)
    assert res["tax"] == {"enabled": False}
    assert res["config"]["apply_tax"] is False
    assert any("Tax is NOT modelled" in l for l in res["limitations"])


def _churn(**kw):
    """A run that actually SELLS: with a rank-exit threshold of 2 in a six-name
    world, holdings are rotated out most weeks."""
    return engine.run_backtest(universe_id="testu", filter_id="always_green",
                               rank_exit_threshold=2, **kw)


def test_tax_never_improves_the_result(market):  # noqa: F811
    gross = _churn(apply_tax=False, cash_yield_pct=0.0)
    net = _churn(apply_tax=True, cash_yield_pct=0.0)

    assert net["tax"]["total_tax_paid"] > 0
    assert net["metrics"]["end_equity"] < gross["metrics"]["end_equity"]


def test_every_sell_reports_its_pnl_and_tax(market):  # noqa: F811
    res = _churn()
    sells = [t for t in res["trades"] if t["side"] == "sell"]
    buys = [t for t in res["trades"] if t["side"] == "buy"]

    assert sells and all(t["pnl"] is not None for t in sells)
    assert all(t["pnl"] is None and t["tax"] == 0.0 for t in buys)
    assert sum(t["tax"] for t in sells) == pytest.approx(
        res["tax"]["capital_gains_tax"], abs=1.0)


def test_parked_cash_interest_is_taxed_at_the_slab_rate(bt_dirs, monkeypatch):  # noqa: F811
    idx = app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker
    price_store.save(idx, _series(900, seed=1))
    price_store.save("S0.NS", _series(900, seed=10))
    universe.save("u", [universe.UniverseMember("S0", "Auto", "MIDSMALLCAP400")])
    monkeypatch.setattr(engine.strategy, "decide_regime",
                        lambda w, f: ("RED", "Supertrend", 1.0))      # always in cash

    res = engine.run_backtest(universe_id="u", cash_yield_pct=7.0, apply_tax=True,
                              starting_capital=1_000_000.0)

    weekly = 1.07 ** (1 / 52) - 1
    weeks = res["metrics"]["weeks"] - 1
    assert res["metrics"]["end_equity"] == pytest.approx(
        1_000_000 * (1 + weekly * 0.70) ** weeks, rel=1e-4)
    assert res["tax"]["capital_gains_tax"] == 0.0
    assert res["tax"]["cash_income_tax"] > 0


def test_after_exit_figures_charge_both_sides_their_exit_tax(market):  # noqa: F811
    res = engine.run_backtest(universe_id="testu", filter_id="always_green")
    t, rules = res["tax"], tax.TaxRules()

    s = t["strategy"]
    assert s["exit_tax"] >= 0
    assert s["end_equity_after_exit"] == pytest.approx(
        res["metrics"]["end_equity"] - s["exit_tax"], abs=0.02)

    curve = res["benchmark_curve"]
    b_start, b_end = curve[0]["equity"], curve[-1]["equity"]
    days = (dt.date.fromisoformat(curve[-1]["date"])
            - dt.date.fromisoformat(curve[0]["date"])).days
    assert t["benchmark"]["exit_tax"] == pytest.approx(
        tax.exit_tax(b_end - b_start, days, rules), abs=0.02)
    assert t["benchmark"]["cagr_after_exit_pct"] == pytest.approx(
        metrics.cagr_pct(b_start, b_end - t["benchmark"]["exit_tax"], len(curve) - 1),
        abs=0.01)


# --- engine: benchmark --------------------------------------------------------

def _tri_file(root, name="midsmall400_tri.csv", start="2020-01-01", n=900):
    close = 100 * np.cumprod(1 + np.full(n, 0.0008))
    d = root / "benchmarks"
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"Date": pd.bdate_range(start, periods=n).strftime("%d %b %Y"),
                  "Total Returns Index": close}).to_csv(d / name, index=False)


def test_the_default_benchmark_is_the_regime_index_and_flagged_price_only(market):  # noqa: F811
    res = engine.run_backtest(universe_id="testu")
    cfg = res["config"]

    assert cfg["benchmark_id"] == app_config.DEFAULT_INDEX_ID == "nifty500"
    assert cfg["benchmark_total_return"] is False
    assert cfg["benchmark_start"] <= cfg["benchmark_end"]
    assert any("PRICE index" in l and "dividends" in l for l in res["limitations"])


def test_a_total_return_file_becomes_the_benchmark(market, bt_dirs):  # noqa: F811
    _tri_file(bt_dirs)
    res = engine.run_backtest(universe_id="testu", benchmark_id="midsmall400_tri")
    cfg = res["config"]

    assert cfg["benchmark_label"] == "NIFTY MIDSMALLCAP 400 TRI"
    assert cfg["benchmark_total_return"] is True
    assert not any("PRICE index" in l for l in res["limitations"])
    assert not any("only starts on" in l for l in res["limitations"])
    # A steady 0.08%/day series: the benchmark's CAGR is known in advance.
    assert res["benchmark"]["cagr_pct"] == pytest.approx(
        (1.0008 ** 5 * 52 / 52) ** 52 * 100 - 100, rel=0.05)


def test_an_etf_proxy_benchmark_is_not_flagged_as_price_only(market):  # noqa: F811
    price_store.save("MIDSMALL.NS", _series(900, seed=3))
    res = engine.run_backtest(universe_id="testu", benchmark_id="midsmall400")
    assert res["config"]["benchmark_total_return"] is True
    assert not any("PRICE index" in l for l in res["limitations"])


def test_a_missing_benchmark_file_fails_before_the_run_starts(market, monkeypatch):  # noqa: F811
    monkeypatch.setattr(engine.strategy, "decide_regime",
                        lambda *a: pytest.fail("the weekly loop must not start"))
    with pytest.raises(engine.BacktestError, match="niftyindices.com"):
        engine.run_backtest(universe_id="testu", benchmark_id="midsmall400_tri")


def test_an_unknown_benchmark_is_a_clear_error(market):  # noqa: F811
    with pytest.raises(engine.BacktestError, match="Unknown benchmark"):
        engine.run_backtest(universe_id="testu", benchmark_id="nope")


def test_a_benchmark_that_starts_late_is_rejected(market, bt_dirs):  # noqa: F811
    _tri_file(bt_dirs, start="2022-06-01", n=300)
    with pytest.raises(engine.BacktestError, match="missing weekly data"):
        engine.run_backtest(universe_id="testu", benchmark_id="midsmall400_tri")


def test_a_benchmark_with_no_data_in_the_window_is_a_clear_error(market, bt_dirs):  # noqa: F811
    _tri_file(bt_dirs, start="2010-01-01", n=50)
    with pytest.raises(engine.BacktestError, match="missing weekly data"):
        engine.run_backtest(universe_id="testu", benchmark_id="midsmall400_tri")


def test_no_cash_yield_means_no_parking_note(market):  # noqa: F811
    res = engine.run_backtest(universe_id="testu", cash_yield_pct=0.0)
    assert not any("liquid or overnight fund" in l for l in res["limitations"])


# --- engine: paths the original suite never reached ---------------------------

def test_open_price_is_none_without_history():
    assert engine._open_price(None, D0) is None
    assert engine._open_price(_series(5).iloc[0:0], D0) is None
    assert engine._open_price(_series(5, start="2024-01-01"), dt.date(2030, 1, 1)) is None


def test_next_session_is_none_after_the_last_trading_day():
    assert engine._next_session([dt.date(2024, 1, 1)], dt.date(2024, 1, 1)) is None


def test_progress_is_reported_every_tenth_week(market):  # noqa: F811
    seen = []
    res = engine.run_backtest(universe_id="testu",
                              progress=lambda done, total, week: seen.append((done, total)))
    total = res["metrics"]["weeks"]
    assert seen[0] == (0, total) and [d for d, _ in seen] == list(range(0, total, 10))


def test_an_end_date_truncates_the_run(market):  # noqa: F811
    res = engine.run_backtest(universe_id="testu", end=dt.date(2022, 6, 30))
    assert res["config"]["end"] <= "2022-06-30"


def test_a_week_without_two_weekly_bars_is_skipped(market, monkeypatch):  # noqa: F811
    """With no warm-up the very first Friday has a single candle — nothing to
    compare against, so the week is skipped rather than decided on noise."""
    monkeypatch.setattr(bt_config, "MIN_WARMUP_DAYS", 0)
    monkeypatch.setattr(engine.strategy, "decide_regime",
                        lambda w, f: ("RED", "Supertrend", 1.0))
    res = engine.run_backtest(universe_id="testu")
    assert res["config"]["start"] == "2020-01-10"
    assert res["weeks"][0]["week_end"] == res["config"]["start"]


def test_a_buy_with_no_price_is_skipped(market, monkeypatch):  # noqa: F811
    real = engine.strategy.apply_caps

    def with_ghost(*a, **k):
        out = real(*a, **k)
        out.buys.insert(0, engine.strategy.RankedName("GHOST", 1, "X"))
        return out

    monkeypatch.setattr(engine.strategy, "apply_caps", with_ghost)
    res = engine.run_backtest(universe_id="testu", filter_id="always_green")
    assert not [t for t in res["trades"] if t["symbol"] == "GHOST"]


def test_a_holding_with_no_cached_prices_is_not_stopped_out(bt_dirs):  # noqa: F811
    pf = Portfolio(cash=0.0, costs=CostModel(0, 0))
    pf.holdings["GONE"] = Holding("GONE", 10, 100.0, D0, stop_price=80.0)
    out = engine._apply_stops(pf, ["GONE.NS"], D0, D0 + dt.timedelta(days=7), 0.0)
    assert out == {"symbols": [], "traded_value": 0.0}
    assert "GONE" in pf.holdings
