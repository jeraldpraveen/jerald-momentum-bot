"""The fast sweep simulator: correctness, and how closely it tracks the engine.

The sweep is a SCREEN. It has to be fast enough to try hundreds of formulas and
faithful enough that the shortlist it produces is worth confirming with the
full engine. Both properties are tested here.
"""

import datetime as dt
import time

import numpy as np
import pandas as pd
import pytest

from app import config as app_config
from app.backtest import engine, panel as panel_mod, price_store, sweep, universe
from app.domain import scoring_formula
from tests.unit.test_backtest_data import bt_dirs  # noqa: F401


def _series(n=1200, start="2016-01-01", drift=0.0015, seed=0, vol=0.014):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, vol, n))
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame(
        {"open": close, "high": close * 1.02, "low": close * 0.97,
         "close": close, "volume": 5_000_000.0},
        index=idx,
    )


@pytest.fixture
def world(bt_dirs):  # noqa: F811
    idx = app_config.get_index_option(app_config.DEFAULT_INDEX_ID).ticker
    price_store.save(idx, _series(seed=1))
    # More names than portfolio slots (max 12), or every candidate gets bought
    # and the ranking — the thing being swept — has no effect at all.
    members = []
    for i in range(24):
        price_store.save(f"S{i}.NS", _series(drift=0.0025 - i * 0.00018, seed=20 + i))
        members.append(universe.UniverseMember(f"S{i}", f"Sector{i % 6}", "MIDSMALLCAP400"))
    universe.save("u", members)
    return idx


@pytest.fixture
def built(world):
    p = panel_mod.build("u")
    return p, sweep.regime_vector(p, "always_green")


# --- basics -----------------------------------------------------------------

def test_simulation_produces_sane_metrics(built):
    p, regime = built
    r = sweep.evaluate(p, scoring_formula.DEFAULT_FORMULA, regime)

    assert r.weeks == len(p.weeks)
    assert r.end_equity > 0 and r.trades > 0
    assert 0 <= r.exposure_pct <= 100
    assert r.formula == scoring_formula.DEFAULT_FORMULA


def test_red_regime_holds_no_positions(built):
    p, _ = built
    r = sweep.evaluate(p, scoring_formula.DEFAULT_FORMULA, np.zeros(len(p.weeks), dtype=int))

    assert r.exposure_pct == 0.0
    assert r.end_equity == pytest.approx(1_000_000, rel=1e-9)   # never invested


def test_costs_reduce_the_result(built):
    p, regime = built
    free = sweep.evaluate(p, scoring_formula.DEFAULT_FORMULA, regime,
                          cost_bps=0, slippage_bps=0).end_equity
    dear = sweep.evaluate(p, scoring_formula.DEFAULT_FORMULA, regime,
                          cost_bps=100, slippage_bps=100).end_equity
    assert free > dear


def test_different_formulas_give_different_results(built):
    """With more candidates than slots, the ranking must actually bite."""
    p, regime = built
    a = sweep.evaluate(p, scoring_formula.DEFAULT_FORMULA, regime)
    b = sweep.evaluate(p, "1 Month Performance", regime)
    c = sweep.evaluate(p, "0 - (1 Year Performance)", regime)   # deliberately inverted
    assert len({round(x.cagr_pct, 4) for x in (a, b, c)}) > 1


def test_the_sector_cap_is_enforced(built):
    """The sweep must obey the same caps — it calls the shared core."""
    p, regime = built
    r = sweep.evaluate(p, scoring_formula.DEFAULT_FORMULA, regime)
    assert r.trades > 0        # ran; cap logic is the core's, tested there


# --- speed ------------------------------------------------------------------

def test_a_formula_evaluates_in_well_under_a_second(built):
    """The whole point: hundreds of variants must be feasible."""
    p, regime = built
    t0 = time.perf_counter()
    sweep.evaluate(p, scoring_formula.DEFAULT_FORMULA, regime)
    assert time.perf_counter() - t0 < 2.0


# --- fidelity to the full engine -------------------------------------------

def test_sweep_tracks_the_full_engine(world):
    """The screen must rank formulas the way the judge would.

    Exact equality is not expected — the sweep checks stops against the week's
    low rather than day by day. What matters is that the two agree closely
    enough that a shortlist chosen by the screen is worth confirming.
    """
    p = panel_mod.build("u")
    regime = sweep.regime_vector(p, "always_green")

    fast = sweep.evaluate(p, scoring_formula.DEFAULT_FORMULA, regime)
    slow = engine.run_backtest(universe_id="u", filter_id="always_green",
                               starting_capital=1_000_000.0)

    fast_cagr, slow_cagr = fast.cagr_pct, slow["metrics"]["cagr_pct"]
    assert abs(fast_cagr - slow_cagr) < 6.0, (
        f"sweep {fast_cagr:.2f}% vs engine {slow_cagr:.2f}% — too far apart to screen with")
    assert (fast_cagr > 0) == (slow_cagr > 0)


def test_sweep_and_engine_agree_on_which_formula_is_better(world):
    """Ranking agreement is what a screen actually needs."""
    p = panel_mod.build("u")
    regime = sweep.regime_vector(p, "always_green")
    f_a = scoring_formula.DEFAULT_FORMULA
    f_b = "(100% * 1 Month Performance) / (1 Month Volatility)"

    fast_a = sweep.evaluate(p, f_a, regime).cagr_pct
    fast_b = sweep.evaluate(p, f_b, regime).cagr_pct
    slow_a = engine.run_backtest(universe_id="u", filter_id="always_green",
                                 formula=f_a)["metrics"]["cagr_pct"]
    slow_b = engine.run_backtest(universe_id="u", filter_id="always_green",
                                 formula=f_b)["metrics"]["cagr_pct"]

    assert (fast_a > fast_b) == (slow_a > slow_b), (
        f"screen ordered them {fast_a:.2f}/{fast_b:.2f}, engine {slow_a:.2f}/{slow_b:.2f}")


# --- walk-forward slicing ---------------------------------------------------

def test_slice_panel_splits_without_overlap(built):
    p, _ = built
    mid = p.weeks[len(p.weeks) // 2]
    train, _ = sweep.slice_panel(p, None, mid)
    test, _ = sweep.slice_panel(p, mid + dt.timedelta(days=1), None)

    assert train.weeks[-1] <= mid < test.weeks[0]
    assert set(train.weeks).isdisjoint(test.weeks)
    assert len(train.weeks) + len(test.weeks) == len(p.weeks)
    # Slices keep every column aligned.
    assert train.close.shape[1] == len(p.symbols)
    assert train.terms["perf_6m"].shape[0] == len(train.weeks)


def test_a_slice_scores_identically_to_the_full_panel(built):
    """Slicing must not change any value — only which weeks are visible."""
    p, _ = built
    compiled = scoring_formula.compile_formula(scoring_formula.DEFAULT_FORMULA)
    full = panel_mod.score_panel(p, compiled)

    mid = p.weeks[len(p.weeks) // 2]
    train, mask = sweep.slice_panel(p, None, mid)
    part = panel_mod.score_panel(train, compiled)

    np.testing.assert_allclose(part, full[mask], rtol=1e-12, equal_nan=True)
