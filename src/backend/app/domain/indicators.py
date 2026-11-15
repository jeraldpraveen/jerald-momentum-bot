"""Regime indicators beyond Supertrend: EMA and MACD.

Each returns a regime ("GREEN"/"RED") plus a single reference number to display.
Pure functions on a weekly close series, no I/O.
"""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def ema_regime(close: pd.Series, period: int) -> tuple[str, float]:
    """GREEN when the latest close is at/above its N-period EMA."""
    last_close = float(close.iloc[-1])
    last_ema = float(ema(close, period).iloc[-1])
    return ("GREEN" if last_close >= last_ema else "RED"), round(last_ema, 2)


def sma(series: pd.Series, period: int) -> float | None:
    """Simple moving average of the last `period` values (None if too short)."""
    if len(series) < period:
        return None
    return float(series.iloc[-period:].mean())


def rsi(close: pd.Series, period: int = 14) -> float | None:
    """Wilder's RSI. None when there isn't enough history, or no price movement."""
    if len(close) < period + 1:
        return None
    delta = close.diff().dropna()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    # Wilder smoothing == EMA with alpha = 1/period.
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    if avg_gain == 0 and avg_loss == 0:
        # A flat series never moved: RSI is 0/0, undefined. Calling that 100
        # ("overbought") struck halted and illiquid names for the wrong reason.
        return None
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(float(100 - (100 / (1 + rs))), 2)


def macd_regime(close: pd.Series, fast: int, slow: int, signal: int) -> tuple[str, float]:
    """GREEN when the MACD histogram (MACD line - signal line) is >= 0.

    Returns (regime, histogram value).
    """
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = float(macd_line.iloc[-1] - signal_line.iloc[-1])
    return ("GREEN" if hist >= 0 else "RED"), round(hist, 2)
