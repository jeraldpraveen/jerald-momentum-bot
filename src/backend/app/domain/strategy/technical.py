"""Technical veto — checks 3/4/5 (Spec v2.2 §2.7) — pure.

Eliminate-only: price below the 50-DMA, more than 30% above the 20-DMA, or
RSI(14) above 82. A name without enough history is "unverified" and PASSES,
per the Rulebook's data-safeguard — a missing number is never a strike.
"""

from __future__ import annotations

import pandas as pd

from ... import config
from ...schemas.fundamental import PASS, STRIKE, UNVERIFIED, CheckResult
from ...schemas.technical import TechnicalCandidate
from .. import indicators
from .types import RankedName


def check_trend(close: float | None, dma50: float | None) -> CheckResult:
    if close is None or dma50 is None:
        return CheckResult(verdict=UNVERIFIED, detail="not enough history for 50-DMA")
    if close < dma50:
        return CheckResult(verdict=STRIKE, value=dma50,
                           detail=f"price {close} below 50-DMA {round(dma50, 2)}")
    return CheckResult(verdict=PASS, value=dma50, detail=f"above 50-DMA {round(dma50, 2)}")


def check_extension(ext_pct: float | None,
                    max_extension_pct: float | None = None) -> CheckResult:
    max_extension_pct = config.MAX_EXTENSION_PCT if max_extension_pct is None else max_extension_pct
    if ext_pct is None:
        return CheckResult(verdict=UNVERIFIED, detail="not enough history for 20-DMA")
    if ext_pct > max_extension_pct:
        return CheckResult(verdict=STRIKE, value=ext_pct,
                           detail=f"{ext_pct}% above 20-DMA > {max_extension_pct}%")
    return CheckResult(verdict=PASS, value=ext_pct, detail=f"{ext_pct}% above 20-DMA")


def check_overbought(rsi_val: float | None, max_rsi: float | None = None) -> CheckResult:
    max_rsi = config.MAX_RSI if max_rsi is None else max_rsi
    if rsi_val is None:
        return CheckResult(verdict=UNVERIFIED, detail="not enough history for RSI(14)")
    if rsi_val > max_rsi:
        return CheckResult(verdict=STRIKE, value=rsi_val,
                           detail=f"RSI(14) {rsi_val} > {max_rsi}")
    return CheckResult(verdict=PASS, value=rsi_val, detail=f"RSI(14) {rsi_val}")


def evaluate_technical(
    candidates: list[RankedName],
    prices: dict[str, pd.DataFrame],
    *,
    trend_period: int | None = None,
    extension_period: int | None = None,
    rsi_period: int | None = None,
    max_extension_pct: float | None = None,
    max_rsi: float | None = None,
) -> list[TechnicalCandidate]:
    """Run the three price checks over the names that survived the fundamentals.

    `prices` is keyed by "<SYMBOL>.NS" and must already be sliced to the as-of
    date — that slicing is what keeps a backtest free of lookahead.
    """
    # Resolved here, not as default args — see the note in caps.apply_caps.
    trend_period = config.DMA_TREND_PERIOD if trend_period is None else trend_period
    extension_period = config.DMA_EXTENSION_PERIOD if extension_period is None else extension_period
    rsi_period = config.RSI_PERIOD if rsi_period is None else rsi_period

    rows: list[TechnicalCandidate] = []

    for c in candidates:
        df = prices.get(f"{c.symbol}.NS")
        close = dma50 = dma20 = ext = rsi_val = None
        if df is not None and not df.empty:
            series = df["close"]
            close = round(float(series.iloc[-1]), 2)
            dma50 = indicators.sma(series, trend_period)
            dma20 = indicators.sma(series, extension_period)
            rsi_val = indicators.rsi(series, rsi_period)
            if dma20:
                ext = round((close / dma20 - 1) * 100, 2)

        checks = {
            "trend": check_trend(close, dma50),
            "extension": check_extension(ext, max_extension_pct),
            "overbought": check_overbought(rsi_val, max_rsi),
        }
        strikes = [r.detail for r in checks.values() if r.verdict == STRIKE]
        flags = [r.detail for r in checks.values() if r.verdict == UNVERIFIED]

        rows.append(TechnicalCandidate(
            symbol=c.symbol, rank=c.rank, sector=c.sector, close=close,
            dma_50=None if dma50 is None else round(dma50, 2),
            dma_20=None if dma20 is None else round(dma20, 2),
            extension_pct=ext, rsi_14=rsi_val,
            struck=bool(strikes), strikes=strikes, flags=flags, **checks,
        ))
    return rows
