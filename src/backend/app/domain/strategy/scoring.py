"""Jerald Score ranking + eligibility (Spec v2.2 §2.4-2.5) — pure.

Given a universe and its price history, produce the ranked candidate list.
Fetching prices and resolving index constituents stays with the caller, so this
function is deterministic: same prices in, same ranking out.

Thresholds are parameters (defaulting to the frozen config values) so the
backtest can sweep them without touching live behaviour.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from ... import config
from ...schemas.scanner import ScannerCandidate, ScannerFilters
from .. import scoring_formula


def median_traded_value_cr(df: pd.DataFrame, window: int) -> float | None:
    """Median of close x volume over the trailing window, in crores."""
    if len(df) < 1:
        return None
    tv = (df["close"] * df["volume"]).tail(window)
    if tv.empty:
        return None
    return float(tv.median() / config.RUPEES_PER_CRORE)


def build_candidates(
    members: list[tuple[str, str, str]],
    prices: dict[str, pd.DataFrame],
    compiled: scoring_formula.CompiledFormula,
    filters: ScannerFilters | None = None,
    *,
    liquidity_floor_cr: float | None = None,
    min_listing_days: int | None = None,
    median_tv_window: int | None = None,
    max_price_age_days: int | None = None,
) -> list[ScannerCandidate]:
    """Score and rank a universe.

    `members` is [(ticker.NS, sector, company)]; `prices` is {ticker: OHLCV
    already sliced to the as-of date}. Eligible names are ranked 1..n by score
    descending; ineligible names keep rank=None and sort after, each carrying
    the flags that explain why.

    When `filters.as_of` is set, a name whose latest bar is more than
    `max_price_age_days` older than it is flagged `stale_price` and not ranked.
    Without that, a halted stock — or a price refresh that silently failed —
    was scored on a series that stopped months ago and ranked as if current.
    """
    # Resolved here, not as default args — see the note in caps.apply_caps.
    liquidity_floor_cr = config.LIQUIDITY_FLOOR_CR if liquidity_floor_cr is None else liquidity_floor_cr
    min_listing_days = config.MIN_LISTING_DAYS if min_listing_days is None else min_listing_days
    median_tv_window = config.MEDIAN_TV_WINDOW if median_tv_window is None else median_tv_window
    max_price_age_days = (config.MAX_PRICE_AGE_DAYS if max_price_age_days is None
                          else max_price_age_days)

    filters = filters or ScannerFilters()
    candidates: list[ScannerCandidate] = []

    for ticker, sector, company in members:
        symbol = ticker[:-3] if ticker.endswith(".NS") else ticker
        df = prices.get(ticker)
        flags: list[str] = []

        if df is None or df.empty:
            candidates.append(ScannerCandidate(
                symbol=symbol, company=company, sector=sector,
                eligible=False, flags=["no_data"]))
            continue

        close = df["close"]
        last_close = float(close.iloc[-1])
        med_tv = median_traded_value_cr(df, median_tv_window)
        listed_days = int(len(df))
        score = scoring_formula.score(close, compiled)

        eligible = True
        if filters.as_of is not None:
            age = (filters.as_of - pd.Timestamp(df.index[-1]).date()).days
            if age > max_price_age_days:
                eligible = False
                flags.append("stale_price")
        if med_tv is None or med_tv < liquidity_floor_cr:
            eligible = False
            flags.append("illiquid")
        if listed_days < min_listing_days:
            eligible = False
            flags.append("listed<12m")
        if np.isnan(score):
            eligible = False
            flags.append("insufficient_history")
        if filters.sectors and sector not in filters.sectors:
            eligible = False
            flags.append("sector_excluded")
        if filters.price_min is not None and last_close < filters.price_min:
            eligible = False
            flags.append("below_price_min")
        if filters.price_max is not None and last_close > filters.price_max:
            eligible = False
            flags.append("above_price_max")

        candidates.append(ScannerCandidate(
            symbol=symbol, company=company, sector=sector,
            close=round(last_close, 2),
            median_tv_cr=None if med_tv is None else round(med_tv, 2),
            listed_days=listed_days,
            score=None if np.isnan(score) else round(float(score), 4),
            eligible=eligible, flags=flags,
        ))

    eligible_sorted = sorted(
        [c for c in candidates if c.eligible],
        key=lambda c: (c.score is None, -(c.score or 0.0)),
    )
    for i, c in enumerate(eligible_sorted, start=1):
        c.rank = i
    ineligible = sorted(
        [c for c in candidates if not c.eligible],
        key=lambda c: (c.score is None, -(c.score or 0.0)),
    )
    return eligible_sorted + ineligible


def as_of_or_today(filters: ScannerFilters | None) -> dt.date:
    return (filters.as_of if filters else None) or dt.date.today()
