"""Step 2.1 — fetch weekly OHLC candles for the NIFTY MIDSMALLCAP 400.

Provider chain, first success wins:
  1. Manual CSV override (data/index_weekly_manual.csv) — authoritative when present.
  2. Official NSE index history API (frequently bot-blocked; attempted politely once).
  3. Yahoo Finance ETF proxy MIDSMALL.NS (Mirae Nifty MidSmallcap 400 ETF).

Every result carries a `source` label so the journal records exactly what fed
the regime decision. Weekly bars are labelled by their FRIDAY (week end) date,
and any still-incomplete week is dropped so the gate only ever sees a true
Friday close.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import pandas as pd
import requests

from . import config

log = logging.getLogger(__name__)

OHLC = ["open", "high", "low", "close"]


@dataclass
class IndexData:
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


def _resample_daily_to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    weekly = daily.resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    weekly.index.name = "week_end"
    return weekly


def _from_manual_csv() -> pd.DataFrame | None:
    """Manual override: CSV with columns date,open,high,low,close (daily or weekly rows)."""
    if not config.MANUAL_CSV.exists():
        return None
    df = pd.read_csv(config.MANUAL_CSV, parse_dates=["date"]).set_index("date").sort_index()
    df = df[OHLC].astype(float)
    return _resample_daily_to_weekly(df)


def _from_nse_api() -> pd.DataFrame | None:
    """Official NSE daily index history. Often returns 403/503 (Akamai); fail quietly."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com/reports-indices-historical-index-data",
    }
    try:
        s = requests.Session()
        s.headers.update(headers)
        s.get("https://www.nseindia.com/reports-indices-historical-index-data", timeout=15)
        to = dt.date.today()
        frm = to - dt.timedelta(days=730)
        r = s.get(
            "https://www.nseindia.com/api/historical/indicesHistory",
            params={
                "indexType": config.INDEX_NAME,
                "from": frm.strftime("%d-%m-%Y"),
                "to": to.strftime("%d-%m-%Y"),
            },
            timeout=20,
        )
        r.raise_for_status()
        recs = r.json()["data"]["indexCloseOnlineRecords"]
        if not recs:
            return None
        df = pd.DataFrame(recs)
        df["date"] = pd.to_datetime(df["EOD_TIMESTAMP"])
        df = df.set_index("date").sort_index()
        df = df.rename(columns={
            "EOD_OPEN_INDEX_VAL": "open", "EOD_HIGH_INDEX_VAL": "high",
            "EOD_LOW_INDEX_VAL": "low", "EOD_CLOSE_INDEX_VAL": "close",
        })[OHLC].astype(float)
        return _resample_daily_to_weekly(df)
    except Exception as exc:  # noqa: BLE001 — any failure falls through the chain
        log.info("NSE API unavailable (%s); falling back", type(exc).__name__)
        return None


def _from_yfinance_proxy() -> pd.DataFrame | None:
    """MIDSMALL.NS ETF daily bars, resampled to W-FRI weekly."""
    try:
        import yfinance as yf

        raw = yf.download(
            config.YF_PROXY_TICKER, period="3y", interval="1d",
            progress=False, auto_adjust=False,
        )
        if raw is None or raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw.rename(columns=str.lower)[OHLC].astype(float)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return _resample_daily_to_weekly(df)
    except Exception as exc:  # noqa: BLE001
        log.info("yfinance proxy unavailable (%s)", type(exc).__name__)
        return None


def fetch_weekly_index_data(now_ist: dt.datetime | None = None) -> IndexData:
    """Step 2.1: weekly candles for the regime gate. Raises if every provider fails."""
    now_ist = now_ist or dt.datetime.now(config.IST)
    providers = [
        ("manual_csv", _from_manual_csv),
        ("nse_official", _from_nse_api),
        (f"yfinance_proxy:{config.YF_PROXY_TICKER}", _from_yfinance_proxy),
    ]
    for source, fn in providers:
        weekly = fn()
        if weekly is None or weekly.empty:
            continue
        weekly = _drop_incomplete_week(weekly, now_ist)
        if len(weekly) < config.MIN_WEEKLY_BARS:
            log.warning("%s returned only %d weekly bars (< %d); trying next provider",
                        source, len(weekly), config.MIN_WEEKLY_BARS)
            continue
        log.info("Index data from %s: %d weekly bars, last week end %s",
                 source, len(weekly), weekly.index[-1].date())
        return IndexData(weekly=weekly, source=source)
    raise RuntimeError(
        "All index data providers failed. Export the index history manually to "
        f"{config.MANUAL_CSV} (columns: date,open,high,low,close) and rerun."
    )
