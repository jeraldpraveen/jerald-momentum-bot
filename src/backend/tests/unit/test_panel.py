"""Feature panel: point-in-time integrity, and agreement with the live scorer.

Two properties matter and both are asserted mechanically:
  1. No lookahead — a term at week t must not move when future bars arrive.
  2. Equivalence — panel scores must match `scoring_formula.score`, or the
     sweep would be optimising something the app does not actually trade.
"""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app import config as app_config
from app.backtest import panel as panel_mod
from app.backtest import price_store, universe
from app.domain import scoring_formula
from tests.unit.test_backtest_data import bt_dirs  # noqa: F401


def _series(n=900, start="2018-01-01", drift=0.0015, seed=0, vol=0.012):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, vol, n))
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame(
        {"open": close, "high": close * 1.02, "low": close * 0.98,
         "close": close, "volume": 5_000_000.0},
        index=idx,
    )


@pytest.fixture
def world(bt_dirs):  # noqa: F811
    """An index plus four names, all with enough history to score."""
    idx = app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker
    price_store.save(idx, _series(seed=1))
    members = []
    for i in range(4):
        price_store.save(f"S{i}.NS", _series(drift=0.002 - i * 0.0005, seed=10 + i))
        members.append(universe.UniverseMember(f"S{i}", f"Sector{i % 2}", "MIDSMALLCAP400"))
    universe.save("u", members)
    return idx


# --- shape and content ------------------------------------------------------

def test_panel_covers_every_term_week_and_symbol(world):
    p = panel_mod.build("u")

    assert p.shape == (len(p.weeks), 4)
    assert set(p.terms) == set(scoring_formula.TERMS)
    for arr in p.terms.values():
        assert arr.shape == p.shape
    assert p.sector[0] == "Sector0"
    assert np.isfinite(p.close).any()


def test_listing_age_and_liquidity_gate_match_the_live_rule(world):
    p = panel_mod.build("u")
    elig = p.eligible()

    # Early weeks have too little history to be eligible; later ones qualify.
    assert not elig[0].any()
    assert elig[-1].all()
    # A punitive floor excludes everything; a zero floor lets history decide.
    assert not p.eligible(liquidity_floor_cr=1e9).any()
    assert p.eligible(liquidity_floor_cr=0.0)[-1].all()


# --- THE leakage test -------------------------------------------------------

def test_panel_has_no_lookahead(world):
    """Appending future bars must not change any past value.

    If this fails, every sweep and every model trained on the panel is invalid:
    a future price would be informing a past decision.
    """
    p1 = panel_mod.build("u")
    cut = p1.weeks[len(p1.weeks) // 2]

    # Extend every stock with 200 wildly different future bars.
    for j in range(4):
        existing = price_store.load(f"S{j}.NS")
        future = _series(200, start="2021-07-05", drift=0.02, seed=99 + j, vol=0.05)
        future.index = pd.bdate_range(existing.index[-1] + pd.Timedelta(days=1), periods=200)
        price_store.save(f"S{j}.NS", pd.concat([existing, future]))
    idx = price_store.load(world)
    idx_future = _series(200, start="2021-07-05", drift=0.02, seed=7, vol=0.05)
    idx_future.index = pd.bdate_range(idx.index[-1] + pd.Timedelta(days=1), periods=200)
    price_store.save(world, pd.concat([idx, idx_future]))
    price_store.clear_memo()

    p2 = panel_mod.build("u", end=cut)
    n = len(p2.weeks)

    assert p2.weeks == p1.weeks[:n]
    for token in scoring_formula.TERMS:
        np.testing.assert_allclose(
            p2.terms[token], p1.terms[token][:n], rtol=1e-12, equal_nan=True,
            err_msg=f"term {token} changed when future bars were appended")
    np.testing.assert_allclose(p2.close, p1.close[:n], rtol=1e-12, equal_nan=True)


def test_a_week_never_uses_a_later_close(world):
    """The value sampled at week t is the last bar ON OR BEFORE t."""
    p = panel_mod.build("u")
    daily = price_store.load("S0.NS")

    for i in (5, len(p.weeks) // 2, len(p.weeks) - 1):
        wk = p.weeks[i]
        upto = daily[daily.index.date <= wk]
        if upto.empty or not np.isfinite(p.close[i, 0]):
            continue
        assert p.close[i, 0] == pytest.approx(float(upto["close"].iloc[-1]))


# --- equivalence with the live scorer ---------------------------------------

def test_panel_scores_match_the_live_scorer(world):
    """The sweep must optimise the same number the app computes."""
    p = panel_mod.build("u")
    compiled = scoring_formula.compile_formula(scoring_formula.DEFAULT_FORMULA)
    fast = panel_mod.score_panel(p, compiled)

    daily = {s: price_store.load(f"{s}.NS") for s in p.symbols}
    for i in (len(p.weeks) - 1, len(p.weeks) - 40, len(p.weeks) // 2):
        wk = p.weeks[i]
        for j, sym in enumerate(p.symbols):
            upto = daily[sym][daily[sym].index.date <= wk]["close"]
            slow = scoring_formula.score(upto, compiled)
            if np.isnan(slow):
                assert np.isnan(fast[i, j])
            else:
                assert fast[i, j] == pytest.approx(slow, rel=1e-9), f"{sym} @ {wk}"


def test_panel_scores_match_for_an_arbitrary_formula(world):
    p = panel_mod.build("u")
    compiled = scoring_formula.compile_formula(
        "(40% * 1 Year Performance) - (2 * 6 Month Volatility)")
    fast = panel_mod.score_panel(p, compiled)

    i = len(p.weeks) - 1
    daily = price_store.load("S1.NS")
    upto = daily[daily.index.date <= p.weeks[i]]["close"]
    assert fast[i, 1] == pytest.approx(scoring_formula.score(upto, compiled), rel=1e-9)


# --- ranking ----------------------------------------------------------------

def test_ranks_are_dense_and_best_first(world):
    p = panel_mod.build("u")
    compiled = scoring_formula.compile_formula(scoring_formula.DEFAULT_FORMULA)
    sc = panel_mod.score_panel(p, compiled)
    rk = panel_mod.ranks(sc, p.eligible())

    i = len(p.weeks) - 1
    live = rk[i][rk[i] > 0]
    assert sorted(live) == list(range(1, len(live) + 1))
    best = int(np.argmax(np.where(rk[i] > 0, -rk[i], -np.inf)))
    assert sc[i, best] == pytest.approx(np.nanmax(sc[i]))


def test_ineligible_names_get_no_rank(world):
    p = panel_mod.build("u")
    sc = panel_mod.score_panel(
        p, scoring_formula.compile_formula(scoring_formula.DEFAULT_FORMULA))
    rk = panel_mod.ranks(sc, p.eligible(liquidity_floor_cr=1e9))
    assert (rk == 0).all()


# --- caching ----------------------------------------------------------------

def test_panel_round_trips_through_the_cache(world):
    p = panel_mod.build("u")
    panel_mod.save(p)
    back = panel_mod.load_cached("u", app_config.DEFAULT_INDEX_ID)

    assert back is not None
    assert back.weeks == p.weeks and back.symbols == p.symbols
    assert back.sector == p.sector and back.meta == p.meta
    for t in p.terms:
        np.testing.assert_allclose(back.terms[t], p.terms[t], equal_nan=True)
    np.testing.assert_allclose(back.close, p.close, equal_nan=True)
    np.testing.assert_allclose(back.week_low, p.week_low, equal_nan=True)


def test_get_reuses_the_cache_instead_of_rebuilding(world, monkeypatch):
    """A 28-minute rebuild must not happen twice for the same inputs."""
    panel_mod.save(panel_mod.build("u"))
    monkeypatch.setattr(panel_mod, "build",
                        lambda *a, **k: pytest.fail("should have used the cache"))
    assert panel_mod.get("u").shape[1] == 4


def test_missing_cache_returns_none(bt_dirs):  # noqa: F811
    assert panel_mod.load_cached("nope", "nifty500") is None
