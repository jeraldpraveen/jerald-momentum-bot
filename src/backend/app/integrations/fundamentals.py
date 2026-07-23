"""Fundamental data for the Part D veto — what can actually be automated.

Interest coverage (EBIT / Interest Expense) IS derivable from Yahoo's income
statement and is auto-fetched here, cached to disk (financials only change
quarterly).

Promoter PLEDGING is deliberately NOT here: Yahoo carries promoter *holding*
(`heldPercentInsiders`) but not *pledging* (% of promoter shares pledged as
collateral), which is an India-specific NSE/BSE quarterly disclosure. Pledging
is therefore a manual input in the live app; per the Rulebook's data-safeguard,
a blank value is "unverified" and passes with a flag rather than striking.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

from .. import config

log = logging.getLogger(__name__)

# yfinance income-statement row names, most preferred first.
_EBIT_ROWS = ("EBIT", "Operating Income", "Pretax Income")
_INTEREST_ROWS = ("Interest Expense", "Interest Expense Non Operating")

NO_DEBT_IC = 999.0   # no/zero interest expense -> effectively infinite coverage


def _load_cache() -> dict:
    path = config.FUNDAMENTALS_CACHE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a corrupt cache must not break the step
        return {}


def _save_cache(cache: dict) -> None:
    path = config.FUNDAMENTALS_CACHE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _fresh(entry: dict, today: dt.date) -> bool:
    try:
        fetched = dt.date.fromisoformat(entry.get("fetched", ""))
    except ValueError:
        return False
    return (today - fetched).days <= config.FUNDAMENTALS_MAX_AGE_DAYS


def _row(fin, names):
    """First matching row's most recent non-null value."""
    for name in names:
        if name in fin.index:
            series = fin.loc[name].dropna()
            if not series.empty:
                return float(series.iloc[0])
    return None


def _fetch_interest_coverage(ticker: str) -> float | None:
    import yfinance as yf

    fin = yf.Ticker(ticker).income_stmt
    if fin is None or fin.empty:
        return None
    ebit = _row(fin, _EBIT_ROWS)
    interest = _row(fin, _INTEREST_ROWS)
    if ebit is None:
        return None
    if interest is None or abs(interest) < 1e-6:
        return NO_DEBT_IC          # no debt servicing cost -> passes
    return round(ebit / abs(interest), 2)


def get_interest_coverage(ticker: str, today: dt.date | None = None) -> float | None:
    """Cached EBIT / |Interest Expense|. None when Yahoo has no usable data."""
    today = today or dt.date.today()
    cache = _load_cache()
    entry = cache.get(ticker)
    if entry and _fresh(entry, today):
        return entry.get("interest_coverage")

    try:
        value = _fetch_interest_coverage(ticker)
    except Exception as exc:  # noqa: BLE001 — best-effort; missing => unverified
        log.info("Interest coverage unavailable for %s (%s)", ticker, type(exc).__name__)
        value = None

    cache[ticker] = {"interest_coverage": value, "fetched": today.isoformat()}
    _save_cache(cache)
    return value


def is_financial(sector: str) -> bool:
    """The interest-coverage check applies to non-financials only."""
    s = (sector or "").lower()
    return any(h in s for h in config.FINANCIAL_SECTOR_HINTS)
