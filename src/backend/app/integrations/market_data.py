"""Step 2.1 — fetch weekly OHLC candles for a chosen index/proxy ticker.

Read-only market-data access (NOT order placement). Provider chain per ticker,
first success wins:
  1. Manual override CSV (data/index_manual/<ticker>.csv) — authoritative.
  2. Yahoo Finance (yfinance) daily bars resampled to weekly (W-FRI).

Weekly bars are labelled by their FRIDAY (week end) date, and any still-open
week is dropped so the gate only ever sees a true Friday close.

A provider whose candles stop before the latest closed week is SKIPPED. The
manual override used to be trusted unconditionally, so a hand-exported file
that was no longer being updated kept feeding the regime gate months-old data.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import pandas as pd

from .. import config

log = logging.getLogger(__name__)

OHLC = ["open", "high", "low", "close"]


@dataclass
class WeeklyIndexData:
    weekly: pd.DataFrame  # index: week-end (Friday) date; columns: open high low close
    source: str


def _drop_incomplete_week(weekly: pd.DataFrame, now_ist: dt.datetime) -> pd.DataFrame:
    """Drop the last bar if its trading week has not closed yet (Fri 15:30 IST)."""
    if weekly.empty:
        return weekly
    week_end = weekly.index[-1]
    close_ts = dt.datetime(
        week_end.year, week_end.month, week_end.day,
        config.MARKET_CLOSE_HOUR, config.MARKET_CLOSE_MINUTE, tzinfo=config.IST,
    )
    if now_ist < close_ts:
        log.warning("Dropping incomplete week ending %s (market not closed yet)", week_end.date())
        return weekly.iloc[:-1]
    return weekly


def _latest_closed_week(now_ist: dt.datetime) -> dt.date:
    """The Friday whose 15:30 close has most recently passed."""
    today = now_ist.date()
    friday = today - dt.timedelta(days=(today.weekday() - 4) % 7)
    close_ts = dt.datetime(friday.year, friday.month, friday.day,
                           config.MARKET_CLOSE_HOUR, config.MARKET_CLOSE_MINUTE,
                           tzinfo=config.IST)
    return friday if now_ist >= close_ts else friday - dt.timedelta(days=7)


def _resample_daily_to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    weekly = daily.resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    weekly.index.name = "week_end"
    return weekly


def _from_manual_csv(ticker: str) -> pd.DataFrame | None:
    path = config.MANUAL_DIR / f"{ticker}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
    df = df[OHLC].astype(float)
    return _resample_daily_to_weekly(df)


def _from_yfinance(ticker: str) -> pd.DataFrame | None:
    try:
        import yfinance as yf

        # Full history so long weekly EMAs (e.g. EMA 200) have enough bars.
        raw = yf.download(ticker, period="max", interval="1d", progress=False, auto_adjust=False)
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw.rename(columns=str.lower)[OHLC].astype(float).dropna()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return _resample_daily_to_weekly(df)
    except Exception as exc:  # noqa: BLE001
        log.info("yfinance unavailable for %s (%s)", ticker, type(exc).__name__)
        return None


def fetch_weekly_index_data(ticker: str, now_ist: dt.datetime | None = None) -> WeeklyIndexData:
    """Weekly candles for `ticker`. Raises if every provider fails."""
    now_ist = now_ist or dt.datetime.now(config.IST)
    providers = [
        (f"manual:{ticker}", lambda: _from_manual_csv(ticker)),
        (f"yfinance:{ticker}", lambda: _from_yfinance(ticker)),
    ]
    expected = _latest_closed_week(now_ist)
    stale: list[str] = []
    for source, fn in providers:
        weekly = fn()
        if weekly is None or weekly.empty:
            continue
        weekly = _drop_incomplete_week(weekly, now_ist)
        if len(weekly) < config.MIN_WEEKLY_BARS:
            log.warning("%s returned only %d weekly bars (< %d)", source, len(weekly), config.MIN_WEEKLY_BARS)
            continue
        last = weekly.index[-1].date()
        if last < expected:
            log.warning("%s stops at the week ending %s, before the latest closed week %s — "
                        "skipped", source, last, expected)
            stale.append(f"{source} (stops {last.isoformat()})")
            continue
        log.info("Index data from %s: %d weekly bars, last week end %s",
                 source, len(weekly), weekly.index[-1].date())
        return WeeklyIndexData(weekly=weekly, source=source)
    if stale:
        raise RuntimeError(
            f"No CURRENT weekly data for {ticker}: the latest closed week ends "
            f"{expected.isoformat()}, but {'; '.join(stale)}. Update or remove "
            f"data/index_manual/{ticker}.csv and retry.")
    raise RuntimeError(
        f"No weekly data for {ticker}. Provide data/index_manual/{ticker}.csv "
        "(columns: date,open,high,low,close) and retry."
    )
