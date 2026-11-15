"""Part A schemas — regime-gate options, run request, and typed result."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

Regime = Literal["GREEN", "RED"]


class IndexOptionOut(BaseModel):
    id: str
    label: str
    ticker: str
    kind: str
    note: str = ""


class FilterOptionOut(BaseModel):
    id: str
    label: str
    indicator: str


class RegimeOptions(BaseModel):
    """Selectable indices + filters offered to the UI dropdowns."""

    indices: list[IndexOptionOut]
    filters: list[FilterOptionOut]
    default_index_id: str
    default_filter_id: str


class RegimeGateRunRequest(BaseModel):
    index_id: Optional[str] = None      # defaults to config.DEFAULT_INDEX_ID
    filter_id: Optional[str] = None     # defaults to config.DEFAULT_FILTER_ID


class RegimeGateOutput(BaseModel):
    """Result of the weekly regime gate (steps 2.1–2.3)."""

    regime: Regime
    week_end: str                       # ISO date of the evaluated Friday close
    index_close: float
    # Generic reference the filter compared against (Supertrend line, EMA value,
    # or MACD histogram). None when filter = "none" (always GREEN).
    line_label: str = ""
    line_value: Optional[float] = None
    index_id: str
    index_label: str
    index_ticker: str
    filter_id: str
    filter_label: str
    indicator: str
    data_source: str                    # which provider fed the decision
    # False when the gate was run on an index other than the ratified one
    # (Spec v2.3: NIFTY 500) — a decision the backtests never covered.
    ratified_index: bool = True
    # False when the candle the gate judged is not for the cycle's own week —
    # the index feed is behind (or the cycle is dated to another week).
    week_matches_cycle: bool = True
    previous_regime: Optional[Regime] = None
    reentry_required: bool = False      # True on a RED -> GREEN transition
    actions: list[str] = []
    active_holdings: list[str] = []
    pending_exit: list[str] = []
