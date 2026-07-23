"""Supertrend (1, 2.5) sanity tests on synthetic weekly data."""

import numpy as np
import pandas as pd
import pytest

from momentum_bot.supertrend import compute_supertrend


def make_bars(closes, spread=1.0):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + spread,
            "low": closes - spread,
            "close": closes,
        },
        index=pd.date_range("2024-01-05", periods=len(closes), freq="W-FRI"),
    )


def test_steady_uptrend_is_green():
    df = make_bars(np.linspace(100, 200, 40))
    st = compute_supertrend(df)
    assert st["trend"].iloc[-1] == 1
    assert st["close"].iloc[-1] > st["supertrend"].iloc[-1]


def test_crash_flips_to_red():
    closes = list(np.linspace(100, 200, 30)) + [150, 140, 130]
    st = compute_supertrend(make_bars(closes))
    assert st["trend"].iloc[-1] == -1
    assert st["close"].iloc[-1] < st["supertrend"].iloc[-1]


def test_recovery_flips_back_to_green():
    closes = list(np.linspace(100, 200, 30)) + [150, 140, 130] + list(np.linspace(135, 220, 15))
    st = compute_supertrend(make_bars(closes))
    assert st["trend"].iloc[-1] == 1


def test_line_sits_below_price_in_uptrend():
    df = make_bars(np.linspace(100, 200, 40))
    st = compute_supertrend(df).iloc[5:]  # skip init transient
    up = st[st["trend"] == 1]
    assert (up["supertrend"] < up["close"]).all()


def test_atr1_line_distance_matches_hand_calc():
    # In a steady rise with spread 1: TR = max(H-L, |H-prevC|, |L-prevC|).
    # Rise per bar r=2, spread s=1 -> TR = H-L = 2 when 2s >= r + s... here
    # H-L=2, H-prevC = r+s = 3, so TR=3; band = hl2 - 2.5*3.
    closes = np.arange(100, 180, 2, dtype=float)
    df = make_bars(closes, spread=1.0)
    st = compute_supertrend(df)
    i = len(df) - 1
    hl2 = (df["high"].iloc[i] + df["low"].iloc[i]) / 2
    expected_band = hl2 - 2.5 * 3.0
    # Final band is the running max of basic lower bands; in a monotone rise
    # the latest basic band is the max.
    assert st["supertrend"].iloc[i] == pytest.approx(expected_band)


def test_too_few_bars_raises():
    with pytest.raises(ValueError):
        compute_supertrend(make_bars([100.0]))
