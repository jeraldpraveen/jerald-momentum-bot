"""Price-cache freshness — the rule that decides whether a valuation is stale."""

import datetime as dt
import os

import pandas as pd
import pytest

from app import config
from app.integrations import stock_prices


def ist(y, m, d, h) -> dt.datetime:
    return dt.datetime(y, m, d, h, tzinfo=config.IST)


def write_cache(ticker: str, last_bar: str, mtime: dt.datetime | None = None) -> None:
    idx = pd.date_range(end=last_bar, periods=3, freq="D")
    df = pd.DataFrame({c: 100.0 for c in stock_prices.OHLCV}, index=idx)
    stock_prices._save(ticker, df)
    if mtime:
        ts = mtime.timestamp()
        os.utime(stock_prices._cache_path(ticker), (ts, ts))


# --- _expected_last_session -------------------------------------------------

def test_before_the_open_expects_yesterdays_bar():
    # Wednesday 07:00 — the session has not started, so Tuesday is the target.
    assert stock_prices._expected_last_session(ist(2026, 7, 22, 7)) == dt.date(2026, 7, 21)


def test_during_the_session_expects_todays_bar():
    """Yahoo publishes a provisional bar from the open — expect it, or the whole
    trading day is valued at yesterday's close."""
    assert stock_prices._expected_last_session(ist(2026, 7, 22, 11)) == dt.date(2026, 7, 22)


def test_after_publish_hour_expects_todays_bar():
    assert stock_prices._expected_last_session(ist(2026, 7, 22, 19)) == dt.date(2026, 7, 22)


def test_weekend_rolls_back_to_friday():
    # Sunday morning and Saturday evening both settle on Friday the 17th.
    assert stock_prices._expected_last_session(ist(2026, 7, 19, 9)) == dt.date(2026, 7, 17)
    assert stock_prices._expected_last_session(ist(2026, 7, 18, 20)) == dt.date(2026, 7, 17)


def test_monday_before_the_open_expects_friday():
    assert stock_prices._expected_last_session(ist(2026, 7, 20, 8)) == dt.date(2026, 7, 17)


# --- _is_fresh --------------------------------------------------------------

def test_missing_cache_is_not_fresh(isolated):
    assert stock_prices._is_fresh("NOPE.NS", now=ist(2026, 7, 22, 19)) is False


def test_cache_holding_the_expected_session_is_fresh(isolated):
    now = ist(2026, 7, 22, 19)
    write_cache("AAA.NS", "2026-07-22", mtime=now - dt.timedelta(hours=6))
    assert stock_prices._is_fresh("AAA.NS", now=now) is True


def test_cache_missing_the_latest_session_is_stale_even_if_written_today(isolated):
    """The original bug: written this morning, two sessions behind, judged fresh."""
    now = ist(2026, 7, 22, 19)
    write_cache("AAA.NS", "2026-07-20", mtime=ist(2026, 7, 22, 7))
    assert stock_prices._is_fresh("AAA.NS", now=now) is False


def test_recent_write_is_rate_limited_not_refetched(isolated):
    """Even a stale cache is left alone briefly, so one run cannot hammer Yahoo."""
    now = ist(2026, 7, 22, 19)
    write_cache("AAA.NS", "2026-07-20", mtime=now - dt.timedelta(minutes=5))
    assert stock_prices._is_fresh("AAA.NS", now=now) is True
    # ...but once the rate-limit window passes, it is stale again.
    assert stock_prices._is_fresh("AAA.NS", now=now + stock_prices.REFETCH_AFTER) is False


def test_yesterdays_bar_is_stale_once_the_session_opens(isolated):
    """The SONACOMS case: cached this morning with Tuesday's close, then Wednesday
    traded and the position stayed valued a full session behind."""
    now = ist(2026, 7, 22, 17)
    write_cache("AAA.NS", "2026-07-21", mtime=ist(2026, 7, 22, 7))
    assert stock_prices._is_fresh("AAA.NS", now=now) is False


def test_todays_bar_is_provisional_until_the_close_is_published(isolated):
    """A mid-session bar keeps moving, so it is re-pulled rather than pinned."""
    write_cache("AAA.NS", "2026-07-22", mtime=ist(2026, 7, 22, 11))
    assert stock_prices._is_fresh("AAA.NS", now=ist(2026, 7, 22, 14)) is False
    # After the publish hour the same bar is final and may be kept.
    assert stock_prices._is_fresh("AAA.NS", now=ist(2026, 7, 22, 19)) is True


def test_provisional_refetch_is_still_rate_limited(isolated):
    """Re-pulling a live bar must not mean a download on every single request."""
    now = ist(2026, 7, 22, 14)
    write_cache("AAA.NS", "2026-07-22", mtime=now - dt.timedelta(minutes=5))
    assert stock_prices._is_fresh("AAA.NS", now=now) is True


def test_get_prices_refetches_a_stale_cache(isolated, monkeypatch):
    write_cache("AAA.NS", "2026-07-15", mtime=ist(2026, 1, 1, 9))
    fresh_idx = pd.date_range(end="2026-07-22", periods=3, freq="D")
    fresh = pd.DataFrame({c: 250.0 for c in stock_prices.OHLCV}, index=fresh_idx)
    calls: list[list[str]] = []

    def fake_download(tickers):
        calls.append(tickers)
        return {t: fresh for t in tickers}

    monkeypatch.setattr(stock_prices, "_download", fake_download)
    out = stock_prices.get_prices(["AAA.NS"])

    assert calls == [["AAA.NS"]]
    assert out["AAA.NS"]["close"].iloc[-1] == pytest.approx(250.0)


def test_force_refetches_even_a_fresh_cache(isolated, monkeypatch):
    """The Refresh LTP button must not be silently answered from the cache.

    Dated relative to TODAY, not a fixed calendar date: `get_prices` consults the
    real clock, so a hardcoded date silently turns this into a refetch test the
    day after it was written.
    """
    today = dt.date.today()
    write_cache("AAA.NS", today.isoformat(),
                mtime=dt.datetime.now(config.IST) - dt.timedelta(minutes=1))
    assert stock_prices._is_fresh("AAA.NS") is True               # cache would serve this

    idx = pd.date_range(end=today, periods=3, freq="D")
    fresh = pd.DataFrame({c: 999.0 for c in stock_prices.OHLCV}, index=idx)
    calls: list[list[str]] = []
    monkeypatch.setattr(stock_prices, "_download",
                        lambda tickers: (calls.append(tickers), {t: fresh for t in tickers})[1])

    assert stock_prices.get_prices(["AAA.NS"])["AAA.NS"]["close"].iloc[-1] == pytest.approx(100.0)
    assert calls == []                                            # served from cache
    out = stock_prices.get_prices(["AAA.NS"], force=True)
    assert calls == [["AAA.NS"]]
    assert out["AAA.NS"]["close"].iloc[-1] == pytest.approx(999.0)
