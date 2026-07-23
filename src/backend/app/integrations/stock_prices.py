"""Per-stock daily OHLCV via yfinance, cached to disk (Part B scoring).

Batch-downloads a whole universe in one threaded `yf.download` call (far faster
than N sequential calls), caches each ticker as CSV, and serves point-in-time
slices (only rows up to an as-of date — no lookahead).
"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from .. import config

log = logging.getLogger(__name__)

OHLCV = ["open", "high", "low", "close", "volume"]


def _cache_path(ticker: str):
    return config.PRICES_DIR / f"{ticker}.csv"


REFETCH_AFTER = dt.timedelta(minutes=30)   # floor on how often one ticker is re-fetched
MARKET_OPEN_HOUR_IST = 9                   # a bar for today exists once the session opens
PUBLISH_HOUR_IST = 18                      # ...and stops moving after this


def _expected_last_session(now: dt.datetime) -> dt.date:
    """The most recent trading day a daily bar should exist for by `now`.

    Once today's session has opened, TODAY is expected — Yahoo publishes a
    provisional bar that tracks the live price. Expecting yesterday instead
    would let a cache written this morning satisfy the check and freeze the
    valuation at yesterday's close for the whole trading day.

    Weekdays only — NSE holidays are not modelled, so on a holiday this asks for
    a bar that will never arrive and the cache simply re-checks every
    REFETCH_AFTER. That costs one download, and never serves a stale price.
    """
    d = now.date()
    if d.weekday() < 5 and now.hour >= MARKET_OPEN_HOUR_IST:
        return d
    d -= dt.timedelta(days=1)              # before the open, or a weekend
    while d.weekday() >= 5:                # Sat/Sun
        d -= dt.timedelta(days=1)
    return d


def _last_bar_date(ticker: str) -> dt.date | None:
    df = _load(ticker)
    if df is None or df.empty:
        return None
    return df.index[-1].date()


def _is_fresh(ticker: str, today: dt.date | None = None, now: dt.datetime | None = None) -> bool:
    """Fresh means the cache actually CONTAINS the latest expected session.

    Judging by file mtime alone (the old rule) silently pinned prices for the
    whole day: a cache written at 07:26, before the previous session's bar was
    published, counted as fresh until midnight and valued the portfolio two
    sessions behind. The mtime is still used, but only as a rate limit.
    """
    p = _cache_path(ticker)
    if not p.exists():
        return False

    now = now or dt.datetime.now(config.IST)
    mtime = dt.datetime.fromtimestamp(p.stat().st_mtime, tz=config.IST)
    if now - mtime < REFETCH_AFTER:
        return True

    last = _last_bar_date(ticker)
    if last is None or last < _expected_last_session(now):
        return False
    # Today's bar is provisional — it keeps moving until the close is published,
    # so it must be re-pulled (subject to the rate limit above) rather than kept.
    return not (last >= now.date() and now.hour < PUBLISH_HOUR_IST)


def _load(ticker: str) -> pd.DataFrame | None:
    p = _cache_path(ticker)
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
    return df[OHLCV] if set(OHLCV).issubset(df.columns) else None


def _save(ticker: str, df: pd.DataFrame) -> None:
    config.PRICES_DIR.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out.index.name = "date"
    out.reset_index().to_csv(_cache_path(ticker), index=False)


def _download(tickers: list[str]) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    if not tickers:
        return {}
    raw = yf.download(
        tickers, period="max", interval="1d", group_by="ticker",
        auto_adjust=True, threads=True, progress=False,
    )
    out: dict[str, pd.DataFrame] = {}
    multi = isinstance(raw.columns, pd.MultiIndex)
    for t in tickers:
        try:
            sub = raw[t] if multi else raw
            df = sub.rename(columns=str.lower)[OHLCV].dropna()
            if not df.empty:
                df.index = pd.to_datetime(df.index).tz_localize(None)
                out[t] = df
        except Exception:  # noqa: BLE001 — a single ticker failing must not sink the batch
            log.info("No price data for %s", t)
    return out


def get_prices(
    tickers: list[str], as_of: dt.date | None = None, today: dt.date | None = None,
    force: bool = False,
) -> dict[str, pd.DataFrame]:
    """Return {ticker: OHLCV up to as_of}. Uses cache; batch-fetches what's stale.

    `force` bypasses both the freshness rule and the rate limit — for the manual
    "refresh LTP" button, where the user is explicitly asking for a live pull.
    """
    today = today or dt.date.today()
    stale = list(tickers) if force else [t for t in tickers if not _is_fresh(t, today)]
    if stale:
        for t, df in _download(stale).items():
            _save(t, df)

    result: dict[str, pd.DataFrame] = {}
    for t in tickers:
        df = _load(t)
        if df is None or df.empty:
            continue
        if as_of is not None:
            df = df[df.index.date <= as_of]
        if not df.empty:
            result[t] = df
    return result
