"""Backtest price store — yfinance download, parquet cache, point-in-time reads.

Separate from `app/integrations/stock_prices.py` on purpose. That store answers
"is today's close in yet?" and re-fetches through the session; this one holds
immutable history, is written once and reused, and its only hard requirement is
**never returning a bar the caller shouldn't be able to see yet**.

That last point is the whole game: `get(ticker, as_of=d)` returns rows strictly
up to and including `d`. Every feature the engine computes goes through it, so
lookahead cannot leak in through a stray `.iloc[-1]` on a full series.
"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from . import config as bt_config

log = logging.getLogger(__name__)

OHLCV = ["open", "high", "low", "close", "volume"]

# Cached history is immutable for the life of a run, and the engine asks for the
# same tickers every week — so read each parquet once. Without this a 400-name,
# 10-year run does ~200k redundant disk reads and crawls.
_MEMO: dict[str, pd.DataFrame] = {}


def clear_memo() -> None:
    """Drop the in-memory cache (after a refresh, or between tests)."""
    _MEMO.clear()


def _path(ticker: str):
    return bt_config.PRICES_DIR / f"{ticker}.parquet"


def is_cached(ticker: str) -> bool:
    return _path(ticker).exists()


def load(ticker: str) -> pd.DataFrame | None:
    """Full cached history for one ticker, or None if never downloaded.

    The index is always named "date" — `save` normalises it — so a load-save-load
    cycle is stable regardless of what the caller handed in.
    """
    memo = _MEMO.get(ticker)
    if memo is not None:
        return memo

    p = _path(ticker)
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    if not set(OHLCV).issubset(df.columns):
        return None
    df.index.name = "date"
    _MEMO[ticker] = df
    return df


def save(ticker: str, df: pd.DataFrame) -> None:
    bt_config.ensure_dirs()
    out = df.copy()
    out.index.name = "date"
    out.to_parquet(_path(ticker))
    _MEMO[ticker] = out          # keep the memo truthful, never stale


# A single session that sits below half (or above double) the local median is a
# vendor error, not a market move: NSE equities have 20% circuit limits, so a
# genuine one-day halving cannot happen. Found live in GOLDBEES.NS, printed at
# 0.336 instead of 33.6 on 2019-12-19/20 — a 100x error that manufactured a
# +10,049% week. Splits are NOT caught here, and should not be: auto_adjust
# handles them, and a missed split is a sustained level shift rather than a spike.
TICK_LOW, TICK_HIGH = 0.5, 2.0
_MEDIAN_WINDOW = 5


def repair_ticks(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Interpolate away obvious bad prints. Returns (frame, sessions repaired)."""
    if df.empty:
        return df, 0
    out = df.copy()
    med = out["close"].rolling(_MEDIAN_WINDOW, center=True, min_periods=2).median()
    bad = (out["close"] < med * TICK_LOW) | (out["close"] > med * TICK_HIGH)
    n = int(bad.sum())
    if n:
        for col in ("open", "high", "low", "close"):
            out.loc[bad, col] = None
            out[col] = out[col].interpolate().bfill().ffill()
    return out, n


def download(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Fetch full history from yfinance for the given tickers."""
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
                df, repaired = repair_ticks(df)
                if repaired:
                    log.warning("%s: repaired %d bad price tick(s) from the vendor",
                                t, repaired)
                out[t] = df
        except Exception:  # noqa: BLE001 — one bad ticker must not sink the batch
            log.info("No backtest price data for %s", t)
    return out


def ensure(tickers: list[str], *, refresh: bool = False, batch: int = 200,
           progress=None) -> dict[str, bool]:
    """Download whatever is missing. Returns {ticker: available}.

    Historical bars do not change, so a cached ticker is never re-fetched unless
    `refresh` is set — that is what makes repeated backtest runs fast and
    offline-capable after the first pull.
    """
    bt_config.ensure_dirs()
    wanted = [t for t in tickers if refresh or not is_cached(t)]
    for i in range(0, len(wanted), batch):
        chunk = wanted[i:i + batch]
        log.info("Backtest price fetch %d-%d of %d", i + 1, i + len(chunk), len(wanted))
        for t, df in download(chunk).items():
            save(t, df)
        if progress:                      # heartbeat, so a long fetch is not
            progress(i + len(chunk), len(wanted))   # mistaken for a dead run
    return {t: is_cached(t) for t in tickers}


def get(ticker: str, as_of: dt.date | None = None) -> pd.DataFrame | None:
    """History for one ticker, truncated at `as_of` (inclusive).

    Returns None when the ticker is uncached or has no rows on or before
    `as_of` — i.e. it had not listed yet. Callers must treat that as "not
    investable this week", never as a zero.
    """
    df = load(ticker)
    if df is None or df.empty:
        return None
    if as_of is not None:
        df = df[df.index.date <= as_of]
    return df if not df.empty else None


def get_many(tickers: list[str], as_of: dt.date | None = None) -> dict[str, pd.DataFrame]:
    """Point-in-time slices for many tickers; absent/unlisted names are omitted."""
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        df = get(t, as_of)
        if df is not None:
            out[t] = df
    return out


def trading_days(ticker: str) -> list[dt.date]:
    """The session dates available for a ticker — used to build the calendar."""
    df = load(ticker)
    return [] if df is None else [d.date() for d in df.index]


def coverage(ticker: str) -> tuple[dt.date, dt.date] | None:
    """(first, last) cached session for a ticker, or None if uncached."""
    df = load(ticker)
    if df is None or df.empty:
        return None
    return df.index[0].date(), df.index[-1].date()
