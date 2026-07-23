"""The regime gate rule (Spec v2.2 §2.1-2.3) — pure.

Takes weekly index candles and a filter definition, returns the verdict. Knows
nothing about cycles, ledgers or who is asking, so the live Friday ritual and a
backtest sweeping 500 historical weeks run the identical decision.
"""

from __future__ import annotations

import pandas as pd

from ... import config
from ..indicators import ema_regime, macd_regime
from ..supertrend import compute_supertrend

GREEN, RED = "GREEN", "RED"


def decide_regime(weekly: pd.DataFrame, filt: config.FilterOption) -> tuple[str, str, float | None]:
    """Return (regime, line_label, line_value) for the chosen filter.

    `weekly` must already be resampled weekly candles with the incomplete
    current week dropped — slicing it to a historical date is the caller's job,
    which is how the backtest avoids lookahead.
    """
    close = weekly["close"]
    if filt.indicator == "none":
        return GREEN, "", None
    if filt.indicator == "supertrend":
        st = compute_supertrend(
            weekly,
            period=int(filt.params.get("period", config.SUPERTREND_ATR_PERIOD)),
            multiplier=float(filt.params.get("multiplier", config.SUPERTREND_MULTIPLIER)),
        )
        last = st.iloc[-1]
        regime = GREEN if last["trend"] == 1 else RED
        return regime, "Supertrend", round(float(last["supertrend"]), 2)
    if filt.indicator == "ema":
        period = int(filt.params["period"])
        regime, value = ema_regime(close, period)
        return regime, f"EMA {period}", value
    if filt.indicator == "macd":
        regime, hist = macd_regime(
            close, int(filt.params["fast"]), int(filt.params["slow"]), int(filt.params["signal"])
        )
        return regime, "MACD histogram", hist
    raise ValueError(f"Unsupported indicator {filt.indicator!r}")
