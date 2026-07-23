"""Supertrend indicator (classic final-band recursion).

With ATR period 1 the ATR of a bar is simply its True Range, which is the
(1, 2.5) parameterisation the rulebook freezes for the weekly regime gate.
Pure functions, no I/O.
"""

import numpy as np
import pandas as pd


def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    n = len(close)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    if period == 1:
        return tr
    atr = np.full(n, np.nan)
    atr[period - 1] = tr[:period].mean()
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def compute_supertrend(df: pd.DataFrame, period: int = 1, multiplier: float = 2.5) -> pd.DataFrame:
    """Return a copy of df with `supertrend` and `trend` (+1 up / -1 down) columns.

    Expects columns: open, high, low, close. Index is the bar timestamp.
    Uptrend (+1) means close is above the Supertrend line -> regime GREEN.
    """
    if len(df) < max(period + 1, 2):
        raise ValueError(f"Need at least {max(period + 1, 2)} bars, got {len(df)}")

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n = len(df)

    atr = wilder_atr(high, low, close, period)
    hl2 = (high + low) / 2.0
    basic_ub = hl2 + multiplier * atr
    basic_lb = hl2 - multiplier * atr

    final_ub = basic_ub.copy()
    final_lb = basic_lb.copy()
    start = period  # first index where ATR is defined for period > 1
    for i in range(start, n):
        if i == start:
            continue
        final_ub[i] = (
            basic_ub[i]
            if basic_ub[i] < final_ub[i - 1] or close[i - 1] > final_ub[i - 1]
            else final_ub[i - 1]
        )
        final_lb[i] = (
            basic_lb[i]
            if basic_lb[i] > final_lb[i - 1] or close[i - 1] < final_lb[i - 1]
            else final_lb[i - 1]
        )

    trend = np.zeros(n, dtype=int)
    line = np.full(n, np.nan)
    trend[start] = 1 if close[start] >= final_ub[start] else -1
    line[start] = final_lb[start] if trend[start] == 1 else final_ub[start]
    for i in range(start + 1, n):
        if trend[i - 1] == 1:
            trend[i] = -1 if close[i] < final_lb[i] else 1
        else:
            trend[i] = 1 if close[i] > final_ub[i] else -1
        line[i] = final_lb[i] if trend[i] == 1 else final_ub[i]

    out = df.copy()
    out["supertrend"] = line
    out["trend"] = trend
    return out
