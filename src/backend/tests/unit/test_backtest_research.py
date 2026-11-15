"""The research machinery: feature panel, fast sweep simulator, run storage.

The sweep simulator is checked against a ledger worked out by hand, because it
is the thing that ranks formulas — if its bookkeeping is off, every sweep is.
Where it (knowingly or not) differs from the full engine or from the live
plan, the difference is pinned or marked `xfail(strict=True)`.
"""

import datetime as dt
import sys
import threading
import types

import numpy as np
import pandas as pd
import pytest

from app import config as app_config
from app.backtest import config as bt_config
from app.backtest import engine, panel as panel_mod, price_store, runs, sweep, universe
from app.domain import scoring_formula
from app.schemas.backtest import (
    BacktestResult, RunRecord, RunStatus, SweepRecord, SweepRunRequest,
)
from app.services import sweep_service
from app.services.errors import StepNotReady
from tests.unit.test_backtest_data import bt_dirs  # noqa: F401
from tests.unit.test_backtest_engine import _series

FRI = dt.date(2024, 1, 5)


def make_panel(close, **over):
    """A hand-built panel: liquid, seasoned names with no technical data, no
    stop-outs and fills at the Friday close unless a test says otherwise."""
    close = np.asarray(close, dtype=float)
    n_w, n_s = close.shape
    nan = lambda: np.full((n_w, n_s), np.nan)  # noqa: E731
    fields = dict(
        weeks=[FRI + dt.timedelta(weeks=i) for i in range(n_w)],
        symbols=[chr(65 + j) for j in range(n_s)],
        terms={t: nan() for t in scoring_formula.TERMS},
        close=close, median_tv_cr=np.full((n_w, n_s), 100.0),
        listed_days=np.full((n_w, n_s), 300.0),
        dma_50=nan(), dma_20=nan(), rsi_14=nan(), week_low=nan(), next_open=nan(),
        sector=[f"Sector{j}" for j in range(n_s)], sleeve=["MIDSMALLCAP400"] * n_s,
        meta={"universe_id": "u", "index_id": "nifty500", "index_ticker": "^CRSLDX"},
    )
    fields.update(over)
    return panel_mod.Panel(**fields)


def sim(p, scores, regime=None, **kw):
    regime = np.ones(len(p.weeks), dtype=int) if regime is None else np.asarray(regime)
    kw.setdefault("cost_bps", 0.0)
    kw.setdefault("slippage_bps", 0.0)
    return sweep.simulate(p, np.asarray(scores, dtype=float), regime, **kw)


# =============================================================================
# sweep simulator — a ledger worked by hand
# =============================================================================

#            week 0   week 1   week 2
CLOSES = [[100.0, 50.0], [110.0, 40.0], [121.0, 44.0]]      # columns: A, B
A_BEATS_B = [[2.0, 1.0]] * 3


def test_three_weeks_of_buying_a_rank_exit_and_a_rebuy_add_up():
    """Capital 10,00,000; 10% a name; no costs; rank-exit threshold 1.

    week 0: buy A 1000 @ 100 and B 2000 @ 50            -> cash 8,00,000
            marked at week 1 closes: 1,10,000 + 80,000  -> equity 9,90,000
    week 1: B ranks 2 (> 1) -> sell 2000 @ 40           -> cash 8,80,000
            a name sold this week is not rebought       -> equity 8,80,000 + 1,21,000
    week 2: B is a fresh candidate again; 10% of 10,01,000 = 1,00,100 -> 2275 @ 44
                                                        -> equity still 10,01,000
    """
    r = sim(make_panel(CLOSES), A_BEATS_B, rank_exit_threshold=1)

    assert r.end_equity == pytest.approx(1_001_000.0)
    assert r.trades == 4                                  # buy, buy, sell, buy
    assert r.weeks == 3 and r.exposure_pct == 100.0
    # turnover = traded 3,80,100 / mean equity / (3/52 years)
    mean_equity = (990_000 + 1_001_000 + 1_001_000) / 3
    assert r.turnover == pytest.approx(380_100 / mean_equity / (3 / 52), abs=0.01)
    assert r.formula == ""                               # set by evaluate(), not simulate()


def test_a_red_week_sells_everything_at_the_next_open():
    p = make_panel(CLOSES, next_open=np.array([[np.nan, np.nan], [108.0, 38.0],
                                               [np.nan, np.nan]]))
    r = sim(p, A_BEATS_B, regime=[1, 0, 0])

    # Sold at Monday's open (108 and 38), not Friday's close (110 and 40).
    assert r.end_equity == pytest.approx(800_000 + 1000 * 108 + 2000 * 38)
    assert r.trades == 4 and r.exposure_pct == pytest.approx(100 / 3, abs=0.01)


def test_slippage_and_charges_are_paid_on_both_legs():
    free = sim(make_panel(CLOSES), A_BEATS_B, regime=[1, 0, 0]).end_equity
    paid = sim(make_panel(CLOSES), A_BEATS_B, regime=[1, 0, 0],
               cost_bps=100, slippage_bps=100).end_equity
    assert paid < free


def test_result_serialises_to_a_plain_dict():
    d = sim(make_panel(CLOSES), A_BEATS_B).as_dict()
    assert set(d) == {"formula", "cagr_pct", "sharpe", "max_drawdown_pct", "volatility_pct",
                      "end_equity", "weeks", "exposure_pct", "turnover", "trades"}


def test_the_three_technical_vetoes_screen_the_shortlist():
    close = [[100.0, 100.0, 100.0, 100.0]] * 2
    p = make_panel(
        close,
        dma_50=np.array([[110.0, 90.0, 90.0, 90.0]] * 2),      # A is below its 50-DMA
        dma_20=np.array([[90.0, 70.0, 90.0, 90.0]] * 2),       # B is 42.9% above its 20-DMA
        rsi_14=np.array([[50.0, 50.0, 90.0, 50.0]] * 2),       # C has RSI 90
    )
    r = sim(p, [[4.0, 3.0, 2.0, 1.0]] * 2)

    assert r.trades == 1                                   # only D survives
    assert r.end_equity == pytest.approx(1_000_000.0)      # bought and marked at 100


def test_the_extension_veto_passes_just_under_thirty_percent_and_strikes_above():
    under = make_panel([[129.9]] * 2, dma_20=np.array([[100.0]] * 2))
    over = make_panel([[130.1]] * 2, dma_20=np.array([[100.0]] * 2))
    assert sim(under, [[1.0]] * 2).trades == 1
    assert sim(over, [[1.0]] * 2).trades == 0


def test_caveat_at_exactly_thirty_percent_the_sweep_strikes_where_the_live_rule_passes():
    """KNOWN DIFFERENCE, pinned — a hair's breadth, but real. 130 / 100 - 1 is
    30.000000000000004 in floating point. The live rule rounds the extension to
    two decimals before comparing (30.0 is not > 30: PASS); the sweep compares
    the raw number (STRIKE)."""
    from app.domain.strategy.technical import check_extension

    assert (130.0 / 100.0 - 1) * 100 > 30.0
    assert check_extension(round((130.0 / 100.0 - 1) * 100, 2)).verdict == "pass"
    p = make_panel([[130.0]] * 2, dma_20=np.array([[100.0]] * 2))
    assert sim(p, [[1.0]] * 2).trades == 0


def test_a_candidate_with_no_usable_price_is_skipped():
    p = make_panel([[0.0, 100.0]] * 2)                     # A's price is zero
    r = sim(p, [[2.0, 1.0]] * 2)
    assert r.trades == 1


def test_an_ineligible_name_is_never_bought():
    p = make_panel([[100.0, 100.0]] * 2,
                   median_tv_cr=np.array([[1.0, 100.0]] * 2))    # A is illiquid
    assert sim(p, [[2.0, 1.0]] * 2).trades == 1


def test_a_holding_that_turns_ineligible_is_exited_like_a_dropped_name():
    tv = np.array([[100.0], [1.0], [1.0]])                 # liquid, then not
    r = sim(make_panel([[100.0], [100.0], [100.0]], median_tv_cr=tv), [[1.0]] * 3)
    assert r.trades == 2 and r.end_equity == pytest.approx(1_000_000.0)


def test_caveat_the_sweep_fills_a_stop_at_the_weeks_low_not_at_the_stop():
    """KNOWN DIFFERENCE from the full engine, pinned (the sweep's own result
    carries a limitation saying so). Stop = 80. The week's low is 70, so the
    sweep books the exit at 70 — even if the price traded straight through 80
    and a real stop would have filled there. It makes the screen pessimistic."""
    p = make_panel([[100.0], [100.0]], week_low=np.array([[70.0], [np.nan]]))
    r = sim(p, [[1.0]] * 2)

    # 1000 sh bought at 100, stopped out at 70 -> 9,70,000. The following Friday
    # the name still ranks first, and nothing stops it being bought straight back.
    assert r.end_equity == pytest.approx(900_000 + 1000 * 70)
    assert r.trades == 3                                   # buy, stop-out, re-buy


def test_a_low_above_the_stop_does_not_trigger_it():
    p = make_panel([[100.0], [100.0]], week_low=np.array([[80.01], [np.nan]]))
    assert sim(p, [[1.0]] * 2).trades == 1


def test_caveat_a_holding_whose_price_disappears_is_valued_at_zero_in_the_sweep():
    """KNOWN DIFFERENCE, pinned: the full engine values an unpriced holding at
    cost ('unknown, not worthless'); the sweep has no price for it and counts
    it as zero, and cannot sell it either."""
    p = make_panel([[100.0], [np.nan], [np.nan]])
    r = sim(p, [[1.0]] * 3)
    assert r.end_equity == pytest.approx(900_000.0)
    assert r.trades == 1


def test_evaluate_scores_a_formula_and_names_the_result(bt_dirs):  # noqa: F811
    p = make_panel(CLOSES)
    p.terms["perf_6m"] = np.array(A_BEATS_B)
    r = sweep.evaluate(p, "6 Month Performance", np.ones(3, dtype=int),
                       cost_bps=0, slippage_bps=0, rank_exit_threshold=1)
    assert r.formula == "6 Month Performance"
    assert r.end_equity == pytest.approx(1_001_000.0)


def test_slice_panel_splits_without_overlap():
    p = make_panel(CLOSES)
    train, tr_mask = sweep.slice_panel(p, None, FRI + dt.timedelta(weeks=1))
    test, te_mask = sweep.slice_panel(p, FRI + dt.timedelta(weeks=2), None)

    assert train.weeks == p.weeks[:2] and test.weeks == p.weeks[2:]
    assert not (tr_mask & te_mask).any()
    assert train.close.shape == (2, 2) and test.terms["perf_6m"].shape == (1, 2)


def test_caveat_the_sweep_calls_a_week_green_when_the_index_has_too_little_history(bt_dirs):  # noqa: F811
    """KNOWN DIFFERENCE, pinned: with fewer than two weekly index bars there is
    no regime to compute. The engine skips such a week; the sweep's vector
    leaves it at its default, which is GREEN."""
    price_store.save("^CRSLDX", _series(30, start="2024-01-01"))
    p = make_panel([[100.0]], weeks=[dt.date(2024, 1, 5)])
    assert list(sweep.regime_vector(p, "st_1_2_5")) == [1]


# Regression test. Before the fix — FIXED CAVEAT (backtest != live): when cash runs
# short the Monday plan DEFERS a buy rather than under-size it ('the strategy holds ~10%
# positions, not more, smaller ones'). The backtest instead spends whatever cash is
# left, so with 12 slots at 10% the last buys are a fraction of a full position. The
# research is not testing exactly the sizing rule that is traded.
def test_the_backtest_never_opens_a_position_below_the_target_size(bt_dirs):  # noqa: F811
    idx = app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker
    price_store.save(idx, _series(500, seed=1))
    members = []
    for i in range(14):
        price_store.save(f"S{i}.NS", _series(500, drift=0.002, seed=40 + i))
        members.append(universe.UniverseMember(f"S{i}", f"Sector{i}", "MIDSMALLCAP400"))
    universe.save("wide", members)

    res = engine.run_backtest(universe_id="wide", filter_id="always_green",
                              cost_bps=0, slippage_bps=0, apply_tax=False, cash_yield_pct=0)
    first_day = res["trades"][0]["date"]
    opening = [t["value"] for t in res["trades"] if t["date"] == first_day]

    # Twelve names were selected, but 10% each only funds ten: ten full positions
    # are opened and the other two are deferred — none is opened as a sliver.
    assert len(opening) == 10
    assert min(opening) >= 0.95 * 100_000
    week = next(w for w in res["weeks"] if w["buys"])       # the first week anything ranks
    assert len(week["buys"]) == 12 and len(week["deferred"]) == 2
    assert set(week["deferred"]) == set(week["buys"]) - set(week["held"])


# =============================================================================
# panel
# =============================================================================

@pytest.fixture
def world(bt_dirs):  # noqa: F811
    idx = app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker
    price_store.save(idx, _series(700, start="2020-01-01", seed=1))
    price_store.save("S0.NS", _series(700, start="2020-01-01", drift=0.002, seed=10))
    universe.save("u", [universe.UniverseMember("S0", "Auto", "MIDSMALLCAP400"),
                        universe.UniverseMember("NOPRICES", "IT", "MICROCAP250")])
    return idx


def test_a_member_with_no_price_history_stays_blank_in_the_panel(world):
    seen = []
    p = panel_mod.build("u", progress=lambda done, total: seen.append((done, total)))

    j = p.symbols.index("NOPRICES")
    assert np.isnan(p.close[:, j]).all() and (p.listed_days[:, j] == 0).all()
    assert not p.eligible()[:, j].any()
    assert np.isfinite(p.close[:, p.symbols.index("S0")]).all()
    assert seen == [(0, 2)]
    assert p.sleeve == ["MIDSMALLCAP400", "MICROCAP250"]


def test_panel_dates_can_be_restricted(world):
    full = panel_mod.build("u")
    part = panel_mod.build("u", start=dt.date(2022, 1, 1), end=dt.date(2022, 6, 30))

    assert part.weeks[0] >= dt.date(2022, 1, 1) and part.weeks[-1] <= dt.date(2022, 6, 30)
    assert 0 < len(part.weeks) < len(full.weeks)


def test_panel_build_errors_are_explicit(world):
    universe.save("empty", [])
    with pytest.raises(engine.BacktestError, match="is empty"):
        panel_mod.build("empty")
    with pytest.raises(engine.BacktestError, match="No weeks in that range"):
        panel_mod.build("u", start=dt.date(2099, 1, 1))
    with pytest.raises(engine.BacktestError, match="No cached history for \\^NSEBANK"):
        panel_mod.build("u", index_id="banknifty")


def test_stock_terms_of_nothing_is_none():
    assert panel_mod._stock_terms(None, [FRI], 20) is None
    assert panel_mod._stock_terms(_series(5).iloc[0:0], [FRI], 20) is None


def test_panel_scores_agree_with_the_live_scorer_including_a_unary_minus(world):
    """The sweep must rank on the SAME number the Friday ritual computes."""
    p = panel_mod.build("u")
    j = p.symbols.index("S0")
    daily = price_store.load("S0.NS")
    for text in ("-6 Month Performance", "+3 Month Performance - 1 Month Volatility",
                 scoring_formula.FROZEN_FORMULA):
        compiled = scoring_formula.compile_formula(text)
        panel_scores = panel_mod.score_panel(p, compiled)[:, j]
        for i in (0, len(p.weeks) // 2, len(p.weeks) - 1):
            upto = daily[daily.index.date <= p.weeks[i]]["close"]
            assert panel_scores[i] == pytest.approx(scoring_formula.score(upto, compiled),
                                                    rel=1e-9), text


def test_an_unsupported_expression_node_is_rejected():
    import ast
    with pytest.raises(scoring_formula.FormulaError, match="Unsupported node Call"):
        panel_mod._eval_array(ast.parse("f(1)", mode="eval").body, {})


def test_ranks_are_dense_and_skip_ineligible_or_unscored_names():
    scores = np.array([[3.0, np.nan, 1.0, 2.0]])
    eligible = np.array([[True, True, False, True]])
    assert panel_mod.ranks(scores, eligible).tolist() == [[1, 0, 0, 2]]


def test_a_panel_is_cached_and_reloaded_identically(world):
    built = panel_mod.get("u")
    again = panel_mod.get("u")

    assert again.weeks == built.weeks and again.symbols == built.symbols
    assert np.array_equal(again.close, built.close, equal_nan=True)
    assert again.meta == built.meta and again.sector == built.sector
    assert panel_mod.load_cached("nope", "nifty500") is None


def test_a_cached_panel_from_an_older_term_set_is_rebuilt(world, monkeypatch):
    panel_mod.get("u")
    monkeypatch.setitem(scoring_formula.TERMS, "perf_2y", ("perf", "12m"))   # vocabulary grew
    assert panel_mod.load_cached("u", app_config.DEFAULT_INDEX_ID) is None


def test_refresh_rebuilds_the_panel_from_current_prices(world):
    old = panel_mod.get("u")
    price_store.save("S0.NS", _series(700, start="2020-01-01", drift=-0.002, seed=99))
    fresh = panel_mod.get("u", refresh=True)
    assert not np.array_equal(fresh.close, old.close, equal_nan=True)


# Regression test. Before the fix — FIXED CAVEAT (stale research): the panel cache is
# keyed by universe and index only. Refresh the prices or rebuild the universe and every
# later sweep still reads the old panel — there is no check that it matches the data it
# was built from, and the Backtest tab has no way to force a rebuild.
def test_a_cached_panel_is_not_reused_after_the_prices_change(world):
    old = panel_mod.get("u")
    price_store.save("S0.NS", _series(700, start="2020-01-01", drift=-0.002, seed=99))
    assert not np.array_equal(panel_mod.get("u").close, old.close, equal_nan=True)


# =============================================================================
# price store
# =============================================================================

def _yf(monkeypatch, download):
    mod = types.ModuleType("yfinance")
    mod.download = download
    monkeypatch.setitem(sys.modules, "yfinance", mod)


def _raw(n=6, close=100.0):
    idx = pd.bdate_range("2024-01-01", periods=n, tz="Asia/Kolkata")
    c = np.full(n, close)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 1e6}, index=idx)


def test_download_of_nothing_asks_for_nothing(monkeypatch):
    _yf(monkeypatch, lambda *a, **k: pytest.fail("must not call yfinance"))
    assert price_store.download([]) == {}


def test_download_repairs_a_bad_print_and_skips_what_is_missing(monkeypatch, caplog):
    bad = _raw(n=9)
    bad.iloc[4, bad.columns.get_loc("Close")] = 1.0            # 100 -> 1: a vendor error
    raw = pd.concat({"OK.NS": _raw(), "BAD.NS": bad, "EMPTY.NS": _raw().iloc[0:0]}, axis=1)
    _yf(monkeypatch, lambda *a, **k: raw)

    with caplog.at_level("WARNING"):
        out = price_store.download(["OK.NS", "BAD.NS", "EMPTY.NS", "MISSING.NS"])

    assert set(out) == {"OK.NS", "BAD.NS"}
    assert out["BAD.NS"]["close"].iloc[4] == pytest.approx(100.0)   # interpolated back
    assert out["OK.NS"].index.tz is None
    assert "repaired 1 bad price tick" in caplog.text


def test_download_handles_the_single_ticker_shape(monkeypatch):
    _yf(monkeypatch, lambda *a, **k: _raw())
    assert len(price_store.download(["OK.NS"])["OK.NS"]) == 6


def test_repair_leaves_an_empty_frame_and_a_real_level_shift_alone():
    empty = _raw().iloc[0:0].rename(columns=str.lower)
    assert price_store.repair_ticks(empty)[1] == 0

    split = _raw(n=20).rename(columns=str.lower)
    split.loc[split.index[10]:, ["open", "high", "low", "close"]] = 40.0     # sustained
    fixed, n = price_store.repair_ticks(split)
    assert n == 0 and fixed["close"].iloc[-1] == 40.0


def test_ensure_fetches_only_what_is_missing_unless_refreshed(bt_dirs, monkeypatch):  # noqa: F811
    price_store.save("HAVE.NS", _series(10))
    asked, beats = [], []
    frame = _series(5)
    monkeypatch.setattr(price_store, "download",
                        lambda tickers: asked.append(list(tickers)) or
                        {t: frame for t in tickers if t != "NODATA.NS"})

    out = price_store.ensure(["HAVE.NS", "NEW.NS", "NODATA.NS"], batch=1,
                             progress=lambda done, total: beats.append((done, total)))

    assert asked == [["NEW.NS"], ["NODATA.NS"]]              # the cached one is not re-pulled
    assert out == {"HAVE.NS": True, "NEW.NS": True, "NODATA.NS": False}
    assert beats == [(1, 2), (2, 2)]

    asked.clear()
    price_store.ensure(["HAVE.NS"], refresh=True)
    assert asked == [["HAVE.NS"]]


def test_a_cached_file_without_ohlcv_columns_is_unusable(bt_dirs):  # noqa: F811
    pd.DataFrame({"close": [1.0]}, index=pd.to_datetime(["2024-01-01"])).to_parquet(
        bt_config.PRICES_DIR / "ODD.NS.parquet")
    assert price_store.load("ODD.NS") is None
    assert price_store.trading_days("ODD.NS") == [] and price_store.coverage("ODD.NS") is None


def test_trading_days_are_the_cached_sessions(bt_dirs):  # noqa: F811
    price_store.save("A.NS", _series(3, start="2024-01-01"))
    assert price_store.trading_days("A.NS") == [dt.date(2024, 1, 1), dt.date(2024, 1, 2),
                                                dt.date(2024, 1, 3)]
    assert price_store.coverage("A.NS") == (dt.date(2024, 1, 1), dt.date(2024, 1, 3))


def test_point_in_time_reads_never_include_a_later_bar(bt_dirs):  # noqa: F811
    price_store.save("A.NS", _series(10, start="2024-01-01"))
    assert price_store.get("A.NS", as_of=dt.date(2023, 12, 31)) is None       # not listed yet
    assert price_store.get_many(["A.NS", "NEVER.NS"], as_of=dt.date(2024, 1, 3)).keys() == {"A.NS"}
    assert price_store.get("A.NS", as_of=dt.date(2024, 1, 3)).index[-1].date() == dt.date(2024, 1, 3)


# =============================================================================
# universes
# =============================================================================

def test_universe_listing_and_loading(bt_dirs, monkeypatch):  # noqa: F811
    universe.save("b", [universe.UniverseMember("X", "S", "MICROCAP250")])
    universe.save("a", [])
    assert universe.list_universes() == ["a", "b"]
    assert universe.microcap_symbols(universe.load("b")) == {"X"}
    with pytest.raises(FileNotFoundError, match="No backtest universe 'nope'"):
        universe.load("nope")

    monkeypatch.setattr(bt_config, "UNIVERSES_DIR", bt_dirs / "not-there")
    assert universe.list_universes() == []


def test_a_universe_file_with_blanks_and_missing_columns_loads_cleanly(bt_dirs):  # noqa: F811
    (bt_config.UNIVERSES_DIR / "hand.csv").write_text(
        "symbol,sector\n tmb ,Banking\n,Nothing\nplain,\n", encoding="utf-8")

    members = universe.load("hand")
    assert [(m.symbol, m.sector, m.sleeve) for m in members] == [
        ("TMB", "Banking", "MIDSMALLCAP400"), ("PLAIN", "", "MIDSMALLCAP400")]
    assert members[0].ticker == "TMB.NS"


def test_building_a_universe_dedupes_and_skips_non_index_sources(bt_dirs, monkeypatch):  # noqa: F811
    micro = app_config.get_universe_option("microcap250").nse_key
    tables = {
        micro: pd.DataFrame({"symbol": ["TINY", "both"], "sector": ["A", "B"]}),
        app_config.get_universe_option("midsmall400").nse_key:
            pd.DataFrame({"symbol": ["BOTH", "MID", " "], "sector": ["B2", "C", "D"]}),
    }
    monkeypatch.setattr(universe.constituents, "fetch_constituents", lambda key: tables[key])

    members = universe.build_from_indices("combo", ["holdings", "microcap250", "midsmall400"])

    assert [(m.symbol, m.sleeve) for m in members] == [
        ("BOTH", "MICROCAP250"),            # first index listing it wins the sleeve
        ("MID", "MIDSMALLCAP400"), ("TINY", "MICROCAP250")]
    assert [m.symbol for m in universe.load("combo")] == ["BOTH", "MID", "TINY"]


# =============================================================================
# run storage
# =============================================================================

@pytest.fixture
def fast_retries(monkeypatch):
    monkeypatch.setattr(runs, "_BACKOFF", 0.0)


def _rec(run_id, status=RunStatus.DONE, updated=None):
    now = updated or dt.datetime(2026, 11, 14, 12, 0)
    return RunRecord(run_id=run_id, status=status, created_at=now, updated_at=now)


def test_two_runs_started_in_the_same_second_get_distinct_ids(bt_dirs):  # noqa: F811
    now = dt.datetime(2026, 11, 14, 12, 0, 0)
    first = runs.new_run_id(now)
    runs.save_run(_rec(first))
    second = runs.new_run_id(now)
    runs.save_run(_rec(second))

    assert (first, second, runs.new_run_id(now)) == (
        "20261114-120000", "20261114-120000.2", "20261114-120000.3")


def test_a_write_that_keeps_colliding_finally_raises(bt_dirs, monkeypatch, fast_retries):  # noqa: F811
    attempts = []

    def locked(src, dst):
        attempts.append(1)
        raise PermissionError("file in use")

    monkeypatch.setattr(runs.os, "replace", locked)
    with pytest.raises(PermissionError):
        runs.save_run(_rec("r1"))
    assert len(attempts) == runs._RETRIES


def test_a_write_that_collides_once_succeeds_on_retry(bt_dirs, monkeypatch, fast_retries):  # noqa: F811
    real, calls = runs.os.replace, []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("file in use")
        return real(src, dst)

    monkeypatch.setattr(runs.os, "replace", flaky)
    runs.save_run(_rec("r1"))
    assert runs.load_run("r1").run_id == "r1" and len(calls) == 2


def test_missing_and_corrupt_records_read_as_none(bt_dirs, fast_retries):  # noqa: F811
    assert runs.load_run("nope") is None
    (bt_config.RUNS_DIR / "bad.json").write_text("{broken", encoding="utf-8")
    assert runs.load_run("bad") is None
    assert runs.load_result("nope") is None
    (bt_config.RESULTS_DIR / "bad.json").write_text("{broken", encoding="utf-8")
    assert runs.load_result("bad") is None


def test_listing_runs_skips_unreadable_files_and_honours_the_limit(bt_dirs, fast_retries,  # noqa: F811
                                                                 monkeypatch):
    for rid in ("20260101-000000", "20260102-000000", "20260103-000000"):
        runs.save_run(_rec(rid))
    (bt_config.RUNS_DIR / "20260104-000000.json").write_text("{broken", encoding="utf-8")

    assert [r.run_id for r in runs.list_runs()] == [
        "20260103-000000", "20260102-000000", "20260101-000000"]
    assert [r.run_id for r in runs.list_runs(limit=1)] == ["20260103-000000"]

    monkeypatch.setattr(bt_config, "RUNS_DIR", bt_dirs / "not-there")
    assert runs.list_runs() == []


def test_results_round_trip_and_delete_removes_both_files(bt_dirs):  # noqa: F811
    runs.save_run(_rec("r1"))
    runs.save_result(BacktestResult(run_id="r1", turnover=6.0, tax={"enabled": False}))

    assert runs.load_result("r1").turnover == 6.0
    assert runs.delete_run("r1") is True
    assert runs.load_run("r1") is None and runs.load_result("r1") is None
    assert runs.delete_run("r1") is False


def test_only_runs_that_stopped_heartbeating_are_marked_interrupted(bt_dirs):  # noqa: F811
    now = dt.datetime(2026, 11, 14, 12, 0)
    runs.save_run(_rec("dead", RunStatus.RUNNING, now - dt.timedelta(minutes=16)))
    runs.save_run(_rec("queued-dead", RunStatus.QUEUED, now - dt.timedelta(hours=3)))
    runs.save_run(_rec("alive", RunStatus.FETCHING, now - dt.timedelta(minutes=14)))
    runs.save_run(_rec("finished", RunStatus.DONE, now - dt.timedelta(days=9)))

    assert runs.mark_stale_as_interrupted(now) == 2
    assert runs.load_run("dead").status == RunStatus.INTERRUPTED
    assert runs.load_run("queued-dead").status == RunStatus.INTERRUPTED
    assert runs.load_run("alive").status == RunStatus.FETCHING
    assert runs.load_run("finished").status == RunStatus.DONE
    assert runs.mark_stale_as_interrupted(now) == 0            # idempotent


# =============================================================================
# sweep job service — the worker, driven directly
# =============================================================================

SPAN = [FRI + dt.timedelta(weeks=i) for i in range(6)]


@pytest.fixture
def worker(bt_dirs, monkeypatch):  # noqa: F811
    """Run `_execute` in-line against a six-week hand-built panel."""
    p = make_panel([[100.0, 50.0]] * 6)
    monkeypatch.setattr(sweep_service.panel_mod, "get",
                        lambda uid, index_id, progress=None: (progress and progress(1, 2), p)[1])
    monkeypatch.setattr(sweep_service.sweep_mod, "regime_vector",
                        lambda panel, filter_id: np.ones(len(panel.weeks), dtype=int))

    def go(cancel=None, evaluate=None, **over):
        body = SweepRunRequest(**{
            "universe_id": "u", "train_end": SPAN[2], "test_start": SPAN[3],
            "weight_step": 50, "noise_samples": 3, **over})
        if evaluate is not None:
            monkeypatch.setattr(sweep_service.sweep_mod, "evaluate", evaluate)
        now = dt.datetime(2026, 11, 14, 12, 0)
        rec = sweep_service.save_record(SweepRecord(
            sweep_id="s1", created_at=now, updated_at=now, request=body.model_dump(mode="json")))
        sweep_service._execute(rec.sweep_id, body, cancel or threading.Event())
        return sweep_service.get_sweep("s1")

    return go


def _result(cagr):
    return sweep.SweepResult("", cagr, 1.0, -10.0, 15.0, 1e6, 3, 100.0, 5.0, 4)


def test_a_sweep_completes_and_reports_winner_baseline_and_noise(worker):
    by_formula = {}

    def evaluate(p, formula, regime, **kw):
        cagr = by_formula.setdefault(formula, 10.0 + len(by_formula))
        return _result(cagr if len(p.weeks) == 3 and p.weeks[0] == SPAN[0] else cagr / 2)

    rec = worker(evaluate=evaluate)

    assert rec.status == RunStatus.DONE and rec.progress.pct == 100.0
    assert rec.noise.n == 3 and rec.noise.spread == pytest.approx(
        rec.noise.max_cagr - rec.noise.min_cagr)
    v = rec.verdict
    result = sweep_service.get_result("s1")
    assert v.formulas_tried == len(result.grid) == len(sweep_service.build_grid(50))
    # The winner is whichever formula did best on TRAIN — never on test.
    best = max(result.grid, key=lambda r: r.train_cagr)
    assert (v.winner_formula, v.winner_train_cagr) == (best.formula, best.train_cagr)
    assert v.winner_test_cagr == pytest.approx(v.winner_train_cagr / 2)
    assert v.baseline_formula == scoring_formula.DEFAULT_FORMULA
    assert v.baseline_train_cagr == by_formula[scoring_formula.DEFAULT_FORMULA]
    assert v.winner_train_edge == pytest.approx(v.winner_train_cagr - v.baseline_train_cagr)

    assert result.config["train_weeks"] == 3 and result.config["test_weeks"] == 3
    assert result.config["train_span"] == [SPAN[0].isoformat(), SPAN[2].isoformat()]
    assert any("SCREEN numbers" in l for l in result.limitations)


def test_the_worker_ignores_a_sweep_whose_record_is_gone(bt_dirs):  # noqa: F811
    body = SweepRunRequest(universe_id="u")
    sweep_service._execute("ghost", body, threading.Event())
    assert sweep_service.get_sweep("ghost") is None


def test_cancelling_before_the_panel_is_ready_stops_the_sweep(worker):
    cancel = threading.Event()
    cancel.set()
    rec = worker(cancel=cancel, evaluate=lambda *a, **k: pytest.fail("must not evaluate"))
    assert rec.status == RunStatus.CANCELLED and rec.error == "Cancelled."


def test_a_split_that_empties_one_side_fails_with_an_explanation(worker):
    rec = worker(train_end=dt.date(2020, 1, 1), test_start=dt.date(2020, 1, 2))
    assert rec.status == RunStatus.FAILED
    assert "leaves one side empty" in rec.error and rec.error.startswith("StepNotReady")


def test_cancelling_during_the_noise_floor_stops_the_sweep(worker):
    cancel = threading.Event()

    def evaluate(p, formula, regime, **kw):
        cancel.set()
        return _result(10.0)

    assert worker(cancel=cancel, evaluate=evaluate).status == RunStatus.CANCELLED


def test_cancelling_during_the_grid_stops_the_sweep(worker):
    cancel = threading.Event()

    def evaluate(p, formula, regime, **kw):
        cancel.set()
        return _result(10.0)

    assert worker(cancel=cancel, evaluate=evaluate, noise_samples=0).status == RunStatus.CANCELLED


def test_degenerate_noise_draws_are_skipped_not_fatal(worker):
    calls = {"n": 0}

    def evaluate(p, formula, regime, **kw):
        calls["n"] += 1
        if calls["n"] <= 3:                                   # every noise draw blows up
            raise ZeroDivisionError("degenerate")
        return _result(12.0 + calls["n"])

    rec = worker(evaluate=evaluate)
    assert rec.status == RunStatus.DONE
    assert rec.noise.n == 0                                   # no noise floor could be measured


# Regression test. Before the fix — FIXED CAVEAT (lost result): if every formula in the
# grid lands on the same CAGR — which is exactly what happens when the test window is
# RED throughout and nothing is ever bought — the train/test correlation is 0/0. That
# NaN is written into the sweep record, the record no longer parses, and the finished
# sweep vanishes from the list and returns 404.
def test_a_sweep_where_every_formula_scores_the_same_can_still_be_read_back(worker):
    rec = worker(evaluate=lambda p, formula, regime, **kw: _result(0.0), noise_samples=0)
    assert rec is not None and rec.status == RunStatus.DONE


def test_a_grid_in_which_nothing_evaluates_fails_with_an_explanation(worker):
    def evaluate(p, formula, regime, **kw):
        raise ValueError("bad formula")

    rec = worker(evaluate=evaluate, noise_samples=0)
    assert rec.status == RunStatus.FAILED
    assert "No formula in the grid produced a result" in rec.error


def test_a_sweep_record_read_mid_rewrite_is_retried_not_reported_missing(bt_dirs, monkeypatch,  # noqa: F811
                                                                         fast_retries):
    """The race behind an intermittent failure in the sweep API tests: the UI
    polls while the worker is replacing the record. One collided read must not
    turn a running sweep into a 404."""
    now = dt.datetime(2026, 11, 14, 12, 0)
    sweep_service.save_record(SweepRecord(sweep_id="s1", created_at=now, updated_at=now,
                                          status=RunStatus.RUNNING))
    real, reads = type(sweep_service._path("s1")).read_text, []

    def collide_once(self, *a, **k):
        reads.append(1)
        if len(reads) == 1:
            raise PermissionError("file is being replaced")
        return real(self, *a, **k)

    monkeypatch.setattr(type(sweep_service._path("s1")), "read_text", collide_once)

    rec = sweep_service.get_sweep("s1")
    assert rec is not None and rec.status == RunStatus.RUNNING
    assert len(reads) == 2


def test_a_record_that_never_becomes_readable_is_given_up_on(bt_dirs, monkeypatch, fast_retries):  # noqa: F811
    reads = []

    def always_locked(self, *a, **k):
        reads.append(1)
        raise PermissionError("locked")

    monkeypatch.setattr(type(sweep_service._path("s1")), "read_text", always_locked)
    assert sweep_service.get_sweep("s1") is None
    assert len(reads) == runs._RETRIES


def test_sweep_records_and_results_that_are_missing_or_corrupt_read_as_none(bt_dirs, monkeypatch,  # noqa: F811
                                                                         fast_retries):
    assert sweep_service.get_sweep("nope") is None and sweep_service.get_result("nope") is None
    (bt_config.RUNS_DIR / "sweep-bad.json").write_text("{broken", encoding="utf-8")
    (bt_config.RESULTS_DIR / "sweep-bad.json").write_text("{broken", encoding="utf-8")

    assert sweep_service.get_sweep("bad") is None and sweep_service.get_result("bad") is None
    assert sweep_service.list_sweeps() == []                  # the corrupt record is skipped
    assert sweep_service.delete_sweep("bad") is True and sweep_service.delete_sweep("bad") is False

    monkeypatch.setattr(bt_config, "RUNS_DIR", bt_dirs / "not-there")
    assert sweep_service.list_sweeps() == []


def test_cancel_reports_whether_a_live_sweep_was_found():
    assert sweep_service.cancel_sweep("nope") is False
    ev = threading.Event()
    sweep_service._CANCEL["live"] = ev
    try:
        assert sweep_service.cancel_sweep("live") is True and ev.is_set()
    finally:
        sweep_service._CANCEL.pop("live", None)


def test_starting_a_sweep_on_an_empty_universe_or_an_overlapping_split_is_refused(bt_dirs):  # noqa: F811
    universe.save("empty", [])
    with pytest.raises(StepNotReady, match="is empty"):
        sweep_service.start_sweep(SweepRunRequest(universe_id="empty"))

    universe.save("u", [universe.UniverseMember("A")])
    with pytest.raises(StepNotReady, match="not out of sample"):
        sweep_service.start_sweep(SweepRunRequest(
            universe_id="u", train_end=dt.date(2020, 1, 1), test_start=dt.date(2020, 1, 1)))


def test_the_formula_grid_has_no_duplicates_and_uses_the_apps_own_syntax():
    grid = sweep_service.build_grid(50)
    assert len(grid) == len(set(grid))
    assert "((100% * 6 Month Performance))" in grid                     # no denominator
    assert ("((50% * 6 Month Performance) + (50% * 3 Month Performance)) "
            "/ (3 Month Volatility)") in grid
    assert len(grid) == 6 * len(sweep_service.VOL_DENOMINATORS)         # 6 weightings x 4
    for formula in grid:
        scoring_formula.compile_formula(formula)                         # every one parses


def test_with_no_read_attempts_allowed_nothing_is_returned(bt_dirs, monkeypatch):  # noqa: F811
    """The fall-through at the end of both retry loops: zero attempts means the
    file is never opened, so the answer is 'not available' even though it exists."""
    runs.save_run(_rec("r1"))
    runs.save_result(BacktestResult(run_id="r1"))
    monkeypatch.setattr(runs, "_RETRIES", 0)

    assert runs.load_run("r1") is None
    assert runs.load_result("r1") is None


# =============================================================================
# more on the fixed caveats
# =============================================================================

def test_the_sweep_defers_a_buy_it_cannot_fund_in_full():
    """Eleven names, 10% each: the eleventh would get the leftover few rupees.
    It is deferred instead, exactly as in the engine and the live plan."""
    close = [[100.0] * 11] * 2
    scores = [[float(11 - j) for j in range(11)]] * 2
    r = sim(make_panel(close), scores)

    assert r.trades == 10                                  # ten full positions, no sliver
    assert r.end_equity == pytest.approx(1_000_000.0)


def test_a_buy_within_five_percent_of_its_target_still_counts_as_full():
    """Capital 1,00,000, two names at 50% each, charges 3%.

    buy 1: 500 sh @ 100 = 50,000 + 1,500 charges      -> cash 48,500
    buy 2: target 50% of 98,500 = 49,250. Cash is 98.5% of that — above the 95%
           line — so it goes ahead, shaved to leave room for charges:
           470 sh = 47,000 + 1,410                    -> cash 90
    """
    assert bt_config.MIN_FILL_FRACTION == 0.95
    r = sim(make_panel([[100.0, 100.0]] * 2), [[2.0, 1.0]] * 2,
            starting_capital=100_000.0, position_weight_pct=50.0, cost_bps=300)
    assert r.trades == 2
    assert r.end_equity == pytest.approx(90 + 500 * 100 + 470 * 100)


def test_a_buy_that_falls_short_of_ninety_five_percent_is_deferred():
    """Same book with 10% charges: buy 1 leaves 45,000; the second target is 50%
    of 95,000 = 47,500 and 95% of that is 45,125 — just out of reach."""
    r = sim(make_panel([[100.0, 100.0]] * 2), [[2.0, 1.0]] * 2,
            starting_capital=100_000.0, position_weight_pct=50.0, cost_bps=1000)
    assert r.trades == 1


def test_a_share_dearer_than_a_whole_position_cannot_be_bought():
    r = sim(make_panel([[200_000.0]] * 2), [[1.0]] * 2)      # target is 1,00,000
    assert r.trades == 0 and r.end_equity == pytest.approx(1_000_000.0)


def test_no_deferrals_are_recorded_when_cash_is_ample(bt_dirs):  # noqa: F811
    idx = app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker
    price_store.save(idx, _series(500, seed=1))
    price_store.save("S0.NS", _series(500, drift=0.002, seed=40))
    universe.save("one", [universe.UniverseMember("S0", "Auto", "MIDSMALLCAP400")])

    res = engine.run_backtest(universe_id="one", filter_id="always_green")
    assert all(w["deferred"] == [] for w in res["weeks"])


def test_one_noise_draw_has_no_spread_and_does_not_break_the_record(worker):
    rec = worker(evaluate=lambda p, formula, regime, **kw:
                 _result(10.0 + len(formula) % 7), noise_samples=1)

    assert rec is not None and rec.status == RunStatus.DONE
    assert rec.noise.n == 1 and rec.noise.std_cagr == 0.0 and rec.noise.spread == 0.0


def test_identical_scores_report_no_train_test_relationship(worker):
    rec = worker(evaluate=lambda p, formula, regime, **kw: _result(0.0), noise_samples=0)
    assert rec.verdict.train_test_corr == 0.0
    assert sweep_service.get_result("s1").verdict.train_test_corr == 0.0


def test_two_formulas_are_too_few_for_a_correlation(worker, monkeypatch):
    monkeypatch.setattr(sweep_service, "build_grid",
                        lambda step: ["6 Month Performance", "3 Month Performance"])
    rec = worker(evaluate=lambda p, formula, regime, **kw: _result(float(len(formula))),
                 noise_samples=0)
    assert rec.status == RunStatus.DONE and rec.verdict.train_test_corr == 0.0
    assert rec.verdict.formulas_tried == 2


# --- price age in the panel (the scanner's stale-price rule, vectorised) ---------

def test_the_panel_records_how_old_each_price_is(bt_dirs):  # noqa: F811
    idx = app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker
    price_store.save(idx, _series(700, start="2020-01-01", seed=1))
    live = _series(700, start="2020-01-01", drift=0.002, seed=10)
    halted = live[live.index.date <= dt.date(2022, 3, 31)]         # stops trading
    price_store.save("LIVE.NS", live)
    price_store.save("HALT.NS", halted)
    universe.save("u", [universe.UniverseMember("LIVE"), universe.UniverseMember("HALT")])

    p = panel_mod.build("u")
    live_j, halt_j = p.symbols.index("LIVE"), p.symbols.index("HALT")
    before = p.weeks.index(next(w for w in p.weeks if w >= dt.date(2022, 3, 25)))
    after = p.weeks.index(next(w for w in p.weeks if w >= dt.date(2022, 5, 1)))

    assert (p.price_age_days[:, live_j] <= 3).all()                # always current
    assert p.price_age_days[before, halt_j] <= 3
    assert p.price_age_days[after, halt_j] > 28                    # a month without a bar
    assert p.eligible()[before, halt_j] and not p.eligible()[after, halt_j]
    assert p.stale()[after, halt_j] and not p.stale()[after, live_j]
    # ...and the old price is still carried forward, which is why age must be checked.
    assert np.isfinite(p.close[after, halt_j])


def test_staleness_limit_is_adjustable_and_absent_without_age_data():
    age = np.array([[3.0, 8.0, np.nan]])
    p = make_panel([[100.0, 100.0, 100.0]], price_age_days=age)
    assert p.stale().tolist() == [[False, True, False]]
    assert p.stale(max_price_age_days=2).tolist() == [[True, True, False]]
    assert p.eligible().tolist() == [[True, False, True]]
    assert not make_panel([[100.0]]).stale().any()                 # no age data: not judged


def test_the_sweep_keeps_a_holding_whose_price_went_stale_instead_of_selling_it():
    """The audit rule, mirrored: a data gap is not an exit signal."""
    age = np.array([[0.0], [30.0], [30.0]])
    r = sim(make_panel([[100.0], [100.0], [100.0]], price_age_days=age), [[1.0]] * 3)
    assert r.trades == 1 and r.exposure_pct == 100.0               # bought once, never sold


def test_the_sweep_never_buys_a_name_whose_price_is_stale():
    age = np.array([[30.0, 0.0]] * 2)
    r = sim(make_panel([[100.0, 100.0]] * 2, price_age_days=age), [[2.0, 1.0]] * 2)
    assert r.trades == 1


def test_price_age_survives_slicing_and_the_cache(world):
    built = panel_mod.get("u")
    again = panel_mod.get("u")
    assert np.array_equal(again.price_age_days, built.price_age_days, equal_nan=True)

    sub, mask = sweep.slice_panel(built, built.weeks[5], built.weeks[9])
    assert sub.price_age_days.shape == (5, len(built.symbols))
    assert np.array_equal(sub.price_age_days, built.price_age_days[mask], equal_nan=True)


def test_a_hand_built_panel_without_age_data_round_trips_through_the_cache(bt_dirs):  # noqa: F811
    p = make_panel(CLOSES)
    panel_mod.save(p)
    back = panel_mod.load_cached("u", "nifty500")
    assert back.price_age_days is None and back.symbols == ["A", "B"]


# --- the panel cache fingerprint ---------------------------------------------------

def test_the_fingerprint_changes_with_prices_the_index_and_the_universe(world):
    stamp = panel_mod.source_stamp("u", app_config.DEFAULT_INDEX_ID)
    assert stamp == panel_mod.source_stamp("u", app_config.DEFAULT_INDEX_ID)      # stable

    price_store.save("S0.NS", _series(701, start="2020-01-01", seed=10))
    after_price = panel_mod.source_stamp("u", app_config.DEFAULT_INDEX_ID)
    price_store.save(world, _series(701, start="2020-01-01", seed=1))
    after_index = panel_mod.source_stamp("u", app_config.DEFAULT_INDEX_ID)
    universe.save("u", [universe.UniverseMember("S0", "Auto", "MIDSMALLCAP400")])
    after_universe = panel_mod.source_stamp("u", app_config.DEFAULT_INDEX_ID)

    assert len({stamp, after_price, after_index, after_universe}) == 4


def test_the_fingerprint_is_none_for_a_universe_that_does_not_exist(bt_dirs):  # noqa: F811
    assert panel_mod.source_stamp("nope", "nifty500") is None


def test_a_panel_cached_before_fingerprints_existed_is_rebuilt(world, caplog):
    old = panel_mod.build("u")
    del old.meta["source_stamp"]                         # as saved by an earlier version
    old.close[:] = -1.0                                  # so a reuse would be obvious
    panel_mod.save(old)

    with caplog.at_level("INFO"):
        fresh = panel_mod.get("u")
    assert (fresh.close[np.isfinite(fresh.close)] > 0).all()
    assert "built from different data; rebuilding" in caplog.text
    assert fresh.meta["source_stamp"] == panel_mod.source_stamp("u", app_config.DEFAULT_INDEX_ID)
