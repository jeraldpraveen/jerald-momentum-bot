"""Jerald momentum-score tests."""

import numpy as np
import pandas as pd
import pytest

from app.domain.momentum_score import (
    ScoreParams,
    momentum_score,
    rank_universe,
    trailing_return,
    trailing_volatility,
)


def series(vals):
    return pd.Series(np.asarray(vals, dtype=float),
                     index=pd.date_range("2024-01-01", periods=len(vals), freq="D"))


def test_trailing_return_exact():
    s = series([100, 105, 110, 120])
    assert trailing_return(s, 1) == pytest.approx(120 / 110 - 1)
    assert trailing_return(s, 3) == pytest.approx(120 / 100 - 1)


def test_trailing_return_nan_when_short():
    assert np.isnan(trailing_return(series([100, 110]), 5))


def test_trailing_volatility_matches_numpy():
    s = series([100, 110, 99, 108, 108])
    rets = s.pct_change().dropna()
    expected = rets.iloc[-3:].std(ddof=1)
    assert trailing_volatility(s, 3) == pytest.approx(expected)


def test_momentum_score_hand_computed():
    # Tiny fixed series with small windows so the value is fully hand-checkable.
    s = series([100, 110, 99, 108])
    p = ScoreParams(
        w6=0.6, w3=0.3, w1=0.1,
        lookback_6m=3, lookback_3m=2, lookback_1m=1,
        vol_window_3m=2, vol_window_1m=2, eps=0.0,
    )
    r6 = 108 / 100 - 1
    r3 = 108 / 110 - 1
    r1 = 108 / 99 - 1
    rets = s.pct_change().dropna()          # [0.10, -0.10, 0.0909091]
    v = rets.iloc[-2:].std(ddof=1)          # both windows use the last 2 returns
    expected = (0.6 * r6 + 0.3 * r3 + 0.1 * r1) / (v + v)

    assert momentum_score(s, p) == pytest.approx(expected)


def test_momentum_score_nan_when_insufficient_history():
    assert np.isnan(momentum_score(series(np.linspace(100, 120, 30))))


def _noisy(rng, n, drift):
    """Deterministic pseudo-random price path with real (non-zero) volatility."""
    steps = 1.0 + drift + rng.normal(0, 0.01, n)
    return series(100 * np.cumprod(steps))


def test_rank_universe_sorts_desc_with_nan_last():
    rng = np.random.default_rng(0)
    a = _noisy(rng, 40, 0.010)
    b = _noisy(rng, 40, 0.002)
    short = series([100, 101, 102])                           # too short -> NaN score
    p = ScoreParams(lookback_6m=30, lookback_3m=15, lookback_1m=5,
                    vol_window_3m=15, vol_window_1m=5)

    ranked = rank_universe({"A": a, "B": b, "SHORT": short}, p)
    scores = ranked["score"].tolist()
    non_nan = [x for x in scores if not np.isnan(x)]
    assert non_nan == sorted(non_nan, reverse=True)
    assert np.isnan(scores[-1])
    assert list(ranked["rank"]) == [1, 2, 3]


def test_weights_change_the_score():
    s = series(list(np.linspace(100, 140, 30)) + list(np.linspace(140, 120, 15)))
    common = dict(lookback_6m=40, lookback_3m=20, lookback_1m=5,
                  vol_window_3m=20, vol_window_1m=5)
    six_heavy = momentum_score(s, ScoreParams(w6=1.0, w3=0.0, w1=0.0, **common))
    one_heavy = momentum_score(s, ScoreParams(w6=0.0, w3=0.0, w1=1.0, **common))
    assert six_heavy != pytest.approx(one_heavy)
