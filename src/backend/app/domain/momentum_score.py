"""Jerald momentum score — a parameterized risk-adjusted momentum score.

A strategy primitive (same category as supertrend.py / indicators.py), shared by
both the live app's selection step (Part B) and the backtest research module —
so neither depends on the other. (The scoring idea was modeled on the Sigma
Scanner, but uses correct lookback windows on split/dividend-adjusted data.)

Base formula (the Jerald Score default weights, no Excel needed):

    Score = (w6 * R6m + w3 * R3m + w1 * R1m) / (Vol3m + Vol1m)

    Rk   = trailing return over k months  = P_t / P_{t-k} - 1
    Volk = std-dev of daily returns over the last k months

Everything is a tunable knob (weights, lookbacks, vol windows, denominator eps)
so the backtest can search for the "best scoring mechanism". Pure functions of a
price series up to date t — no lookahead by construction (the caller slices the
series to the as-of date). Dependency-light (pandas/numpy only).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# NSE ≈ 21 trading days per month.
TRADING_DAYS_PER_MONTH = 21


@dataclass(frozen=True)
class ScoreParams:
    """Tunable knobs for the Jerald momentum score (defaults = base weights)."""

    w6: float = 0.60
    w3: float = 0.30
    w1: float = 0.10
    lookback_6m: int = 6 * TRADING_DAYS_PER_MONTH   # 126
    lookback_3m: int = 3 * TRADING_DAYS_PER_MONTH   # 63
    lookback_1m: int = 1 * TRADING_DAYS_PER_MONTH   # 21
    vol_window_3m: int = 3 * TRADING_DAYS_PER_MONTH  # 63
    vol_window_1m: int = 1 * TRADING_DAYS_PER_MONTH  # 21
    eps: float = 0.0                                 # denominator guard


def trailing_return(close: pd.Series, lookback: int) -> float:
    """P_t / P_{t-lookback} - 1. NaN if history is too short."""
    if lookback <= 0 or len(close) <= lookback:
        return float("nan")
    return float(close.iloc[-1] / close.iloc[-1 - lookback] - 1.0)


def trailing_volatility(close: pd.Series, window: int) -> float:
    """Sample std-dev of daily simple returns over the last `window` days."""
    if window < 2:
        return float("nan")
    rets = close.pct_change().dropna()
    if len(rets) < window:
        return float("nan")
    return float(rets.iloc[-window:].std(ddof=1))


def momentum_score(close: pd.Series, params: ScoreParams | None = None) -> float:
    """Risk-adjusted momentum score for a single stock's close series (as-of t).

    Returns NaN when any required window lacks enough history or the volatility
    denominator is zero — such names are simply ranked last, never picked.
    """
    p = params or ScoreParams()
    r6 = trailing_return(close, p.lookback_6m)
    r3 = trailing_return(close, p.lookback_3m)
    r1 = trailing_return(close, p.lookback_1m)
    v3 = trailing_volatility(close, p.vol_window_3m)
    v1 = trailing_volatility(close, p.vol_window_1m)

    if np.isnan([r6, r3, r1, v3, v1]).any():
        return float("nan")

    numerator = p.w6 * r6 + p.w3 * r3 + p.w1 * r1
    denominator = v3 + v1 + p.eps
    if denominator == 0:
        return float("nan")
    return numerator / denominator


def rank_universe(
    prices: dict[str, pd.Series], params: ScoreParams | None = None
) -> pd.DataFrame:
    """Score every symbol and return a rank table (best score = rank 1).

    `prices` maps symbol -> close series already sliced to the as-of date.
    NaN scores sort last. Columns: symbol, score, rank.
    """
    p = params or ScoreParams()
    rows = [{"symbol": sym, "score": momentum_score(close, p)} for sym, close in prices.items()]
    df = pd.DataFrame(rows, columns=["symbol", "score"])
    df = df.sort_values("score", ascending=False, na_position="last").reset_index(drop=True)
    df["rank"] = df.index + 1
    return df
