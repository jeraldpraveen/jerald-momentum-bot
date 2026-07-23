"""EMA and MACD regime indicators."""

import numpy as np
import pandas as pd

from app.domain.indicators import ema, ema_regime, macd_regime


def series(vals):
    return pd.Series(np.asarray(vals, dtype=float))


def test_ema_matches_pandas_ewm():
    s = series(np.arange(1, 50))
    assert ema(s, 10).iloc[-1] == s.ewm(span=10, adjust=False).mean().iloc[-1]


def test_ema_regime_green_when_close_above():
    s = series(np.linspace(100, 200, 60))  # steadily rising -> close above EMA
    regime, value = ema_regime(s, 20)
    assert regime == "GREEN"
    assert value < s.iloc[-1]


def test_ema_regime_red_when_close_below():
    s = series(list(np.linspace(100, 200, 60)) + list(np.linspace(195, 130, 20)))
    regime, _ = ema_regime(s, 20)
    assert regime == "RED"


def test_macd_regime_green_in_uptrend():
    s = series(np.linspace(100, 300, 120))
    regime, hist = macd_regime(s, 35, 70, 12)
    assert regime == "GREEN"
    assert hist >= 0


def test_macd_regime_red_after_rollover():
    s = series(list(np.linspace(100, 300, 120)) + list(np.linspace(295, 180, 40)))
    regime, hist = macd_regime(s, 35, 70, 12)
    assert regime == "RED"
    assert hist < 0
