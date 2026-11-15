"""Market-data integrations — every network call faked, every fallback exercised.

These modules decide what the strategy SEES. The rule they must obey is the
Rulebook's data-safeguard: a number that could not be fetched is "unverified",
never silently replaced by a stale or default one. Tests marked
`xfail(strict=True)` are places where the code breaks that rule today.
"""

import datetime as dt
import json
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

from app import config
from app.integrations import constituents, fundamentals, market_data, stock_prices, surveillance

IST = config.IST


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point every cache this layer writes at a throwaway directory."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "UNIVERSES_DIR", tmp_path / "universes")
    monkeypatch.setattr(config, "PRICES_DIR", tmp_path / "prices")
    monkeypatch.setattr(config, "MANUAL_DIR", tmp_path / "index_manual")
    monkeypatch.setattr(config, "FUNDAMENTALS_CACHE", tmp_path / "fundamentals_cache.json")
    monkeypatch.setattr(config, "SURVEILLANCE_CACHE", tmp_path / "surveillance_cache.json")
    return tmp_path


class FakeResponse:
    def __init__(self, text="", payload=None, status=200):
        self.text, self._payload, self.status = text, payload, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self._payload


def fake_yf(monkeypatch, *, download=None, income_stmt=None):
    """Install a stand-in `yfinance` module for the lazy imports to find."""
    mod = types.ModuleType("yfinance")
    mod.download = download or (lambda *a, **k: pd.DataFrame())
    mod.Ticker = lambda ticker: types.SimpleNamespace(income_stmt=income_stmt(ticker)
                                                      if callable(income_stmt) else income_stmt)
    monkeypatch.setitem(sys.modules, "yfinance", mod)
    return mod


def bars(n=5, start="2026-11-09", close=100.0, tz=None):
    idx = pd.bdate_range(start, periods=n, tz=tz)
    c = np.full(n, close) + np.arange(n)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c,
                         "Volume": 1_000_000.0}, index=idx)


# =============================================================================
# constituents
# =============================================================================

NSE_CSV = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
           " Sansera Engineering Ltd. ,Automobile and Auto Components, SANSERA ,EQ,INE1\n"
           "Kirloskar Oil Eng Ltd.,Capital Goods,KIRLOSENG,EQ,INE2\n")


def test_constituents_are_fetched_trimmed_and_cached(data_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(constituents.requests, "get",
                        lambda url, **kw: calls.append(url) or FakeResponse(NSE_CSV))

    df = constituents.fetch_constituents("ind_niftymidsmallcap400list")

    assert calls == ["https://archives.nseindia.com/content/indices/"
                     "ind_niftymidsmallcap400list.csv"]
    assert list(df["symbol"]) == ["SANSERA", "KIRLOSENG"]              # whitespace stripped
    assert list(df["sector"]) == ["Automobile and Auto Components", "Capital Goods"]
    assert df["company"].iloc[0] == "Sansera Engineering Ltd."
    assert (data_dir / "universes" / "ind_niftymidsmallcap400list.csv").exists()


def test_cached_constituents_are_served_without_touching_the_network(data_dir, monkeypatch):
    monkeypatch.setattr(constituents.requests, "get", lambda url, **kw: FakeResponse(NSE_CSV))
    constituents.fetch_constituents("k")
    monkeypatch.setattr(constituents.requests, "get",
                        lambda *a, **k: pytest.fail("must not refetch a cached list"))

    assert list(constituents.fetch_constituents("k")["symbol"]) == ["SANSERA", "KIRLOSENG"]


def test_force_refresh_replaces_the_cached_list(data_dir, monkeypatch):
    monkeypatch.setattr(constituents.requests, "get", lambda url, **kw: FakeResponse(NSE_CSV))
    constituents.fetch_constituents("k")
    newer = NSE_CSV + "New Co,IT,NEWCO,EQ,INE3\n"
    monkeypatch.setattr(constituents.requests, "get", lambda url, **kw: FakeResponse(newer))

    assert "NEWCO" in list(constituents.fetch_constituents("k", force_refresh=True)["symbol"])
    assert "NEWCO" in list(constituents.fetch_constituents("k")["symbol"])     # cache updated


def test_an_http_error_is_raised_and_nothing_is_cached(data_dir, monkeypatch):
    monkeypatch.setattr(constituents.requests, "get",
                        lambda url, **kw: FakeResponse(status=403))
    with pytest.raises(RuntimeError, match="HTTP 403"):
        constituents.fetch_constituents("k")
    assert not (data_dir / "universes" / "k.csv").exists()


# Regression test. Before the fix — FIXED CAVEAT: the constituent cache never expires.
# NSE reshuffles these indices every March and September, but a list cached once is
# served forever — the live files in this project were fetched on 22 July 2026 and
# already predate a reconstitution. Names dropped from the index are still scanned, new
# members are never seen, and 'dropped from both indices' cannot trigger.
def test_a_constituent_list_older_than_a_reconstitution_is_refetched(data_dir, monkeypatch):
    monkeypatch.setattr(constituents.requests, "get", lambda url, **kw: FakeResponse(NSE_CSV))
    constituents.fetch_constituents("k")
    cache = data_dir / "universes" / "k.csv"
    a_year_ago = (dt.datetime.now() - dt.timedelta(days=365)).timestamp()
    os.utime(cache, (a_year_ago, a_year_ago))

    newer = NSE_CSV + "New Co,IT,NEWCO,EQ,INE3\n"
    monkeypatch.setattr(constituents.requests, "get", lambda url, **kw: FakeResponse(newer))
    assert "NEWCO" in list(constituents.fetch_constituents("k")["symbol"])


# =============================================================================
# fundamentals (interest coverage)
# =============================================================================

def stmt(**rows):
    """An income statement the way yfinance returns it: newest column first."""
    cols = pd.to_datetime(["2026-03-31", "2025-03-31"])
    return pd.DataFrame({c: [v[i] for v in rows.values()] for i, c in enumerate(cols)},
                        index=[k.replace("_", " ") for k in rows])


def test_interest_coverage_is_ebit_over_the_size_of_interest_expense(monkeypatch):
    fake_yf(monkeypatch, income_stmt=stmt(EBIT=[500.0, 300.0], Interest_Expense=[-100.0, -90.0]))
    assert fundamentals._fetch_interest_coverage("X.NS") == 5.0        # newest year, |−100|


def test_operating_income_stands_in_when_ebit_is_missing(monkeypatch):
    fake_yf(monkeypatch, income_stmt=stmt(Operating_Income=[240.0, 1.0],
                                          Interest_Expense=[100.0, 1.0]))
    assert fundamentals._fetch_interest_coverage("X.NS") == 2.4


def test_a_row_that_is_all_blank_is_skipped_for_the_next_candidate(monkeypatch):
    fake_yf(monkeypatch, income_stmt=stmt(EBIT=[np.nan, np.nan], Operating_Income=[300.0, 1.0],
                                          Interest_Expense=[np.nan, 150.0]))
    # EBIT is blank -> Operating Income; interest's newest year is blank -> prior year.
    assert fundamentals._fetch_interest_coverage("X.NS") == 2.0


def test_negative_ebit_gives_negative_coverage_which_will_strike(monkeypatch):
    fake_yf(monkeypatch, income_stmt=stmt(EBIT=[-50.0, 1.0], Interest_Expense=[100.0, 1.0]))
    assert fundamentals._fetch_interest_coverage("X.NS") == -0.5
    assert -0.5 < config.MIN_INTEREST_COVERAGE


def test_no_statement_means_no_coverage_figure(monkeypatch):
    fake_yf(monkeypatch, income_stmt=None)
    assert fundamentals._fetch_interest_coverage("X.NS") is None
    fake_yf(monkeypatch, income_stmt=pd.DataFrame())
    assert fundamentals._fetch_interest_coverage("X.NS") is None


def test_no_earnings_row_means_no_coverage_figure(monkeypatch):
    fake_yf(monkeypatch, income_stmt=stmt(Total_Revenue=[1.0, 1.0],
                                          Interest_Expense=[100.0, 1.0]))
    assert fundamentals._fetch_interest_coverage("X.NS") is None


def test_zero_interest_expense_is_treated_as_debt_free(monkeypatch):
    fake_yf(monkeypatch, income_stmt=stmt(EBIT=[500.0, 1.0], Interest_Expense=[0.0, 0.0]))
    assert fundamentals._fetch_interest_coverage("X.NS") == fundamentals.NO_DEBT_IC == 999.0


# Regression test. Before the fix — FIXED CAVEAT (wrong arithmetic): when a statement
# has neither 'EBIT' nor 'Operating Income', the code falls back to 'Pretax Income' and
# divides THAT by interest. Pretax income is already AFTER interest, so the result is
# the true coverage minus one. A company earning 2.5x its interest is reported at 1.5x
# and struck by the 2.0 floor.
def test_pretax_income_fallback_adds_interest_back_before_dividing(monkeypatch):
    # EBIT 250, interest 100 -> pretax 150. True coverage = 250 / 100 = 2.5.
    fake_yf(monkeypatch, income_stmt=stmt(Pretax_Income=[150.0, 1.0],
                                          Interest_Expense=[100.0, 1.0]))
    assert fundamentals._fetch_interest_coverage("X.NS") == 2.5


# Regression test. Before the fix — FIXED CAVEAT (data-safeguard): a statement with NO
# interest-expense row is reported as coverage 999 — 'debt free, verified pass'. Yahoo
# often omits that row for Indian companies, so this is missing data being presented as
# a cited clean result. It should be unverified (None), which also passes, but with the
# flag the Rulebook requires.
def test_a_missing_interest_row_is_unverified_not_debt_free(monkeypatch):
    fake_yf(monkeypatch, income_stmt=stmt(EBIT=[500.0, 1.0]))
    assert fundamentals._fetch_interest_coverage("X.NS") is None


def test_coverage_is_fetched_once_then_served_from_the_cache(data_dir, monkeypatch):
    calls = []
    monkeypatch.setattr(fundamentals, "_fetch_interest_coverage",
                        lambda t: calls.append(t) or 3.5)
    today = dt.date(2026, 11, 14)

    assert fundamentals.get_interest_coverage("X.NS", today) == 3.5
    assert fundamentals.get_interest_coverage("X.NS", today + dt.timedelta(days=30)) == 3.5
    assert calls == ["X.NS"]                                       # 30 days old: still fresh
    saved = json.loads((data_dir / "fundamentals_cache.json").read_text())
    assert saved == {"X.NS": {"interest_coverage": 3.5, "fetched": "2026-11-14"}}


def test_a_cache_entry_older_than_thirty_days_is_refetched(data_dir, monkeypatch):
    values = iter([3.5, 1.2])
    monkeypatch.setattr(fundamentals, "_fetch_interest_coverage", lambda t: next(values))
    today = dt.date(2026, 11, 14)

    fundamentals.get_interest_coverage("X.NS", today)
    assert fundamentals.get_interest_coverage("X.NS", today + dt.timedelta(days=31)) == 1.2


def test_a_failed_fetch_returns_none_instead_of_raising(data_dir, monkeypatch):
    def boom(t):
        raise ConnectionError("yahoo down")

    monkeypatch.setattr(fundamentals, "_fetch_interest_coverage", boom)
    assert fundamentals.get_interest_coverage("X.NS", dt.date(2026, 11, 14)) is None


# Regression test. Before the fix — FIXED CAVEAT: a FAILED fetch is written to the cache
# like a real answer and kept for 30 days. One Yahoo outage on a Friday leaves that
# company 'unverified' for a month even though the very next request would have
# succeeded.
def test_a_failed_fetch_is_retried_on_the_next_request(data_dir, monkeypatch):
    def boom(t):
        raise ConnectionError("yahoo down")

    monkeypatch.setattr(fundamentals, "_fetch_interest_coverage", boom)
    fundamentals.get_interest_coverage("X.NS", dt.date(2026, 11, 14))
    monkeypatch.setattr(fundamentals, "_fetch_interest_coverage", lambda t: 4.0)

    assert fundamentals.get_interest_coverage("X.NS", dt.date(2026, 11, 15)) == 4.0


def test_a_corrupt_fundamentals_cache_is_ignored(data_dir, monkeypatch):
    (data_dir / "fundamentals_cache.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(fundamentals, "_fetch_interest_coverage", lambda t: 2.2)
    assert fundamentals._load_cache() == {}
    assert fundamentals.get_interest_coverage("X.NS", dt.date(2026, 11, 14)) == 2.2


def test_a_cache_entry_without_a_valid_date_is_not_fresh():
    today = dt.date(2026, 11, 14)
    assert fundamentals._fresh({"fetched": "garbage"}, today) is False
    assert fundamentals._fresh({}, today) is False
    assert fundamentals._fresh({"fetched": "2026-11-14"}, today) is True


@pytest.mark.parametrize("sector,expected", [
    ("Financial Services", True), ("Private Bank", True), ("Life Insurance", True),
    ("NBFC", True), ("Capital Goods", False), ("", False), (None, False),
])
def test_interest_cover_applies_to_non_financials_only(sector, expected):
    assert fundamentals.is_financial(sector) is expected


# =============================================================================
# surveillance (ASM / GSM)
# =============================================================================

ASM = {"longterm": {"data": [{"symbol": " hfcl ", "asmSurvIndicator": "Stage I"}]},
       "shortterm": {"data": [{"symbol": "CUPID", "survDesc": "ST Stage II"},
                              {"symbol": "", "asmSurvIndicator": "x"}]},
       "note": "ignored"}
GSM = [{"symbol": "BADCO", "gsmStage": "Stage IV"}, {"symbol": "PLAIN"}]


def _nse(monkeypatch, asm=ASM, gsm=GSM):
    def get(url, **kw):
        payload = asm if url.endswith("reportASM") else gsm
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(payload=payload)

    monkeypatch.setattr(surveillance.requests, "get", get)


def test_rows_flattens_both_payload_shapes():
    assert surveillance._rows(GSM) == GSM
    assert len(surveillance._rows(ASM)) == 3
    assert surveillance._rows("unexpected") == [] and surveillance._rows(None) == []


def test_surveillance_lists_are_merged_labelled_and_cached(data_dir, monkeypatch):
    _nse(monkeypatch)
    today = dt.date(2026, 11, 13)

    out = surveillance.fetch_surveillance(today)

    assert out == {
        "HFCL": "NSE ASM (Stage I)",          # symbol trimmed and upper-cased
        "CUPID": "NSE ASM (ST Stage II)",     # falls back to survDesc
        "BADCO": "NSE GSM (Stage IV)",
        "PLAIN": "NSE GSM (GSM)",             # no stage given
    }
    cached = json.loads((data_dir / "surveillance_cache.json").read_text())
    assert cached == {"fetched": "2026-11-13", "symbols": out}


def test_an_asm_row_with_no_stage_is_labelled_plain_asm(data_dir, monkeypatch):
    _nse(monkeypatch, asm={"longterm": {"data": [{"symbol": "X"}]}}, gsm=[])
    assert surveillance.fetch_surveillance(dt.date(2026, 11, 13)) == {"X": "NSE ASM (ASM)"}


def test_the_same_day_cache_is_served_without_a_request(data_dir, monkeypatch):
    _nse(monkeypatch)
    today = dt.date(2026, 11, 13)
    first = surveillance.fetch_surveillance(today)
    monkeypatch.setattr(surveillance.requests, "get",
                        lambda *a, **k: pytest.fail("must not refetch on the same day"))

    assert surveillance.fetch_surveillance(today) == first


def test_yesterdays_cache_is_refetched(data_dir, monkeypatch):
    _nse(monkeypatch)
    surveillance.fetch_surveillance(dt.date(2026, 11, 12))
    _nse(monkeypatch, asm={}, gsm=[{"symbol": "NEWLY", "survDesc": "Stage I"}])

    assert surveillance.fetch_surveillance(dt.date(2026, 11, 13)) == {
        "NEWLY": "NSE GSM (Stage I)"}


def test_a_corrupt_surveillance_cache_triggers_a_refetch(data_dir, monkeypatch):
    (data_dir / "surveillance_cache.json").write_text("{broken", encoding="utf-8")
    _nse(monkeypatch)
    assert "HFCL" in surveillance.fetch_surveillance(dt.date(2026, 11, 13))


def test_when_nse_is_unreachable_nothing_is_cached(data_dir, monkeypatch):
    _nse(monkeypatch, asm=ConnectionError("blocked"))
    assert surveillance.fetch_surveillance(dt.date(2026, 11, 13)) is None
    assert not (data_dir / "surveillance_cache.json").exists()


def test_an_http_error_from_nse_counts_as_unreachable(data_dir, monkeypatch):
    monkeypatch.setattr(surveillance.requests, "get",
                        lambda url, **kw: FakeResponse(status=403))
    assert surveillance.fetch_surveillance(dt.date(2026, 11, 13)) is None


# Regression test. Before the fix — FIXED CAVEAT (data-safeguard): when NSE cannot be
# reached the function returns an empty dict — exactly what it returns when NO stock is
# under surveillance. The caller cannot tell 'checked, all clear' from 'could not
# check', so the veto goes on to report names as 'not on ASM/GSM' with no check behind
# it.
def test_unreachable_is_distinguishable_from_no_stock_under_surveillance(data_dir, monkeypatch):
    _nse(monkeypatch, asm={}, gsm=[])
    all_clear = surveillance.fetch_surveillance(dt.date(2026, 11, 12))
    _nse(monkeypatch, asm=ConnectionError("blocked"))
    unreachable = surveillance.fetch_surveillance(dt.date(2026, 11, 13))

    assert all_clear == {}
    assert unreachable != all_clear


def test_a_partial_failure_discards_the_half_that_did_load(data_dir, monkeypatch):
    """KNOWN BEHAVIOUR, pinned: ASM loads, GSM fails -> the whole result is
    reported as 'could not check'. Nothing is struck on half a list, and nothing
    is reported clear on half a list either."""
    _nse(monkeypatch, gsm=ConnectionError("blocked"))
    assert surveillance.fetch_surveillance(dt.date(2026, 11, 13)) is None


# =============================================================================
# index candles for the regime gate
# =============================================================================

def _weekly(n=40, last="2026-11-13"):
    idx = pd.date_range(end=last, periods=n, freq="W-FRI")
    c = np.linspace(100, 140, n)
    return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c}, index=idx)


def _manual_csv(data_dir, ticker, daily):
    d = data_dir / "index_manual"
    d.mkdir(parents=True, exist_ok=True)
    out = daily.rename(columns=str.lower)[["open", "high", "low", "close"]]
    out.index.name = "date"
    out.reset_index().to_csv(d / f"{ticker}.csv", index=False)


def test_a_week_is_incomplete_until_friday_1530_ist():
    weekly = _weekly()
    before = dt.datetime(2026, 11, 13, 15, 29, tzinfo=IST)
    after = dt.datetime(2026, 11, 13, 15, 30, tzinfo=IST)

    assert len(market_data._drop_incomplete_week(weekly, before)) == len(weekly) - 1
    assert len(market_data._drop_incomplete_week(weekly, after)) == len(weekly)
    assert market_data._drop_incomplete_week(weekly.iloc[0:0], after).empty


def test_daily_bars_become_friday_labelled_weeks():
    daily = bars(n=7, start="2026-11-02").rename(columns=str.lower)       # Mon..Fri, Mon, Tue
    weekly = market_data._resample_daily_to_weekly(daily)

    assert [d.date().isoformat() for d in weekly.index] == ["2026-11-06", "2026-11-13"]
    first = weekly.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"]) == (100, 105, 99, 104)
    assert weekly.index.name == "week_end"


def test_manual_csv_is_none_when_absent_and_weekly_when_present(data_dir):
    assert market_data._from_manual_csv("^CRSLDX") is None
    _manual_csv(data_dir, "^CRSLDX", bars(n=10, start="2026-11-02"))
    assert len(market_data._from_manual_csv("^CRSLDX")) == 2


def test_yfinance_daily_bars_are_flattened_and_resampled(monkeypatch):
    raw = bars(n=10, start="2026-11-02", tz="Asia/Kolkata")
    raw.columns = pd.MultiIndex.from_product([raw.columns, ["^CRSLDX"]])     # yfinance shape
    fake_yf(monkeypatch, download=lambda *a, **k: raw)

    weekly = market_data._from_yfinance("^CRSLDX")
    assert len(weekly) == 2 and weekly.index.tz is None
    assert list(weekly.columns) == ["open", "high", "low", "close"]


def test_yfinance_returning_nothing_or_failing_is_none(monkeypatch):
    fake_yf(monkeypatch, download=lambda *a, **k: pd.DataFrame())
    assert market_data._from_yfinance("X") is None
    fake_yf(monkeypatch, download=lambda *a, **k: None)
    assert market_data._from_yfinance("X") is None

    def boom(*a, **k):
        raise ConnectionError("yahoo down")

    fake_yf(monkeypatch, download=boom)
    assert market_data._from_yfinance("X") is None


def _providers(monkeypatch, manual=None, yahoo=None):
    monkeypatch.setattr(market_data, "_from_manual_csv", lambda t: manual)
    monkeypatch.setattr(market_data, "_from_yfinance", lambda t: yahoo)


NOW = dt.datetime(2026, 11, 14, 10, 0, tzinfo=IST)            # Saturday after the close


def test_the_manual_override_wins_over_yahoo(monkeypatch):
    _providers(monkeypatch, manual=_weekly(40), yahoo=_weekly(50))
    out = market_data.fetch_weekly_index_data("^CRSLDX", NOW)
    assert out.source == "manual:^CRSLDX" and len(out.weekly) == 40


def test_yahoo_is_used_when_there_is_no_manual_file(monkeypatch):
    _providers(monkeypatch, manual=None, yahoo=_weekly(50))
    out = market_data.fetch_weekly_index_data("^CRSLDX", NOW)
    assert out.source == "yfinance:^CRSLDX" and len(out.weekly) == 50


def test_a_provider_with_too_little_history_is_skipped(monkeypatch):
    _providers(monkeypatch, manual=_weekly(config.MIN_WEEKLY_BARS - 1), yahoo=_weekly(35))
    assert market_data.fetch_weekly_index_data("^CRSLDX", NOW).source == "yfinance:^CRSLDX"


def test_no_usable_provider_is_a_clear_error(monkeypatch):
    _providers(monkeypatch, manual=None, yahoo=_weekly(5))
    with pytest.raises(RuntimeError, match=r"No weekly data for \^CRSLDX.*index_manual"):
        market_data.fetch_weekly_index_data("^CRSLDX", NOW)
    _providers(monkeypatch, manual=_weekly(40).iloc[0:0], yahoo=None)
    with pytest.raises(RuntimeError):
        market_data.fetch_weekly_index_data("^CRSLDX")           # defaults to now


def test_the_still_open_week_never_reaches_the_gate(monkeypatch):
    _providers(monkeypatch, manual=None, yahoo=_weekly(40, last="2026-11-13"))
    thursday = dt.datetime(2026, 11, 12, 16, 0, tzinfo=IST)
    out = market_data.fetch_weekly_index_data("^CRSLDX", thursday)
    assert out.weekly.index[-1].date() == dt.date(2026, 11, 6)


# Regression test. Before the fix — FIXED CAVEAT (stale gate): the manual override is
# 'authoritative' with no freshness check. A hand-exported index file left in
# data/index_manual keeps feeding the regime gate after it stops being updated — the
# gate then decides GREEN/RED on candles that are months old, with Yahoo's fresh data
# never consulted.
def test_a_manual_file_that_stopped_months_ago_does_not_feed_this_weeks_gate(monkeypatch):
    _providers(monkeypatch, manual=_weekly(40, last="2026-07-24"),
               yahoo=_weekly(40, last="2026-11-13"))
    out = market_data.fetch_weekly_index_data("^CRSLDX", NOW)
    assert out.weekly.index[-1].date() == dt.date(2026, 11, 13)


# =============================================================================
# per-stock prices
# =============================================================================

def _cache(data_dir, ticker, frame, written: dt.datetime | None = None):
    stock_prices._save(ticker, frame.rename(columns=str.lower))
    if written is not None:
        ts = written.timestamp()
        os.utime(data_dir / "prices" / f"{ticker}.csv", (ts, ts))


def test_download_of_nothing_asks_for_nothing(monkeypatch):
    fake_yf(monkeypatch, download=lambda *a, **k: pytest.fail("must not call yfinance"))
    assert stock_prices._download([]) == {}


def test_download_splits_a_multi_ticker_frame_and_drops_bad_rows(monkeypatch):
    a = bars(n=4, tz="Asia/Kolkata")
    b = bars(n=4, close=50.0, tz="Asia/Kolkata")
    b.iloc[1, b.columns.get_loc("Close")] = np.nan                 # a hole in B
    raw = pd.concat({"A.NS": a, "B.NS": b, "EMPTY.NS": a.iloc[0:0]}, axis=1)
    fake_yf(monkeypatch, download=lambda *args, **kw: raw)

    out = stock_prices._download(["A.NS", "B.NS", "EMPTY.NS", "MISSING.NS"])

    assert set(out) == {"A.NS", "B.NS"}                            # empty + missing skipped
    assert len(out["A.NS"]) == 4 and len(out["B.NS"]) == 3
    assert list(out["A.NS"].columns) == stock_prices.OHLCV
    assert out["A.NS"].index.tz is None


def test_download_handles_the_single_ticker_shape(monkeypatch):
    fake_yf(monkeypatch, download=lambda *a, **k: bars(n=3))
    assert len(stock_prices._download(["A.NS"])["A.NS"]) == 3


def test_get_prices_skips_names_with_no_cache_and_names_not_yet_listed(data_dir, monkeypatch):
    monkeypatch.setattr(stock_prices, "_download", lambda tickers: {})
    _cache(data_dir, "A.NS", bars(n=5, start="2026-11-09"))

    out = stock_prices.get_prices(["A.NS", "NEVER.NS"], as_of=dt.date(2026, 11, 11))
    assert list(out) == ["A.NS"] and out["A.NS"].index[-1].date() == dt.date(2026, 11, 11)
    # Before A's first bar there is nothing to show — it is omitted, not zeroed.
    assert stock_prices.get_prices(["A.NS"], as_of=dt.date(2026, 11, 1)) == {}


def test_force_redownloads_even_a_fresh_cache(data_dir, monkeypatch):
    _cache(data_dir, "A.NS", bars(n=5, start="2026-11-09"))
    asked = []
    monkeypatch.setattr(stock_prices, "_download",
                        lambda tickers: asked.append(list(tickers)) or
                        {"A.NS": bars(n=6, start="2026-11-09").rename(columns=str.lower)})

    out = stock_prices.get_prices(["A.NS"], force=True)
    assert asked == [["A.NS"]] and len(out["A.NS"]) == 6


def test_no_cache_means_no_last_bar_and_not_fresh(data_dir):
    assert stock_prices._load("A.NS") is None
    assert stock_prices._last_bar_date("A.NS") is None
    assert stock_prices._is_fresh("A.NS") is False


def test_a_cache_file_missing_a_column_is_unusable(data_dir):
    d = data_dir / "prices"
    d.mkdir(parents=True)
    (d / "A.NS.csv").write_text("date,close\n2026-11-13,100\n", encoding="utf-8")
    assert stock_prices._load("A.NS") is None


@pytest.mark.parametrize("now,expected", [
    (dt.datetime(2026, 11, 13, 9, 0, tzinfo=IST), dt.date(2026, 11, 13)),    # Fri, open
    (dt.datetime(2026, 11, 13, 8, 59, tzinfo=IST), dt.date(2026, 11, 12)),   # Fri, pre-open
    (dt.datetime(2026, 11, 14, 12, 0, tzinfo=IST), dt.date(2026, 11, 13)),   # Saturday
    (dt.datetime(2026, 11, 15, 12, 0, tzinfo=IST), dt.date(2026, 11, 13)),   # Sunday
    (dt.datetime(2026, 11, 16, 8, 0, tzinfo=IST), dt.date(2026, 11, 13)),    # Mon, pre-open
])
def test_the_session_a_cache_is_expected_to_contain(now, expected):
    assert stock_prices._expected_last_session(now) == expected


def test_a_cache_ending_before_the_expected_session_is_stale(data_dir):
    saturday = dt.datetime(2026, 11, 14, 12, 0, tzinfo=IST)
    _cache(data_dir, "A.NS", bars(n=4, start="2026-11-09"),              # ends Thursday
           written=saturday - dt.timedelta(hours=2))
    assert stock_prices._is_fresh("A.NS", now=saturday) is False

    _cache(data_dir, "B.NS", bars(n=5, start="2026-11-09"),              # ends Friday
           written=saturday - dt.timedelta(hours=2))
    assert stock_prices._is_fresh("B.NS", now=saturday) is True


def test_todays_bar_is_provisional_until_six_pm(data_dir):
    _cache(data_dir, "A.NS", bars(n=5, start="2026-11-09"),
           written=dt.datetime(2026, 11, 13, 10, 0, tzinfo=IST))
    assert stock_prices._is_fresh("A.NS", now=dt.datetime(2026, 11, 13, 16, 0, tzinfo=IST)) is False
    assert stock_prices._is_fresh("A.NS", now=dt.datetime(2026, 11, 13, 18, 0, tzinfo=IST)) is True


def test_caveat_a_cache_written_in_the_last_half_hour_is_trusted_whatever_it_holds(data_dir):
    """KNOWN BEHAVIOUR, pinned — and a real risk at the Friday close. The 30-minute
    rate limit is checked BEFORE the content: a cache written at 15:20 with a
    mid-session price for today is 'fresh' at 15:45, so a ritual started straight
    after the bell scores on a bar that is not the closing price. Nothing marks
    the bar as provisional to the scanner."""
    written = dt.datetime(2026, 11, 13, 15, 20, tzinfo=IST)
    _cache(data_dir, "A.NS", bars(n=4, start="2026-11-09"), written=written)   # no Friday bar at all

    assert stock_prices._is_fresh("A.NS", now=written + dt.timedelta(minutes=25)) is True
    assert stock_prices._is_fresh("A.NS", now=written + dt.timedelta(minutes=31)) is False


def test_caveat_a_failed_refresh_serves_the_stale_cache_with_no_signal(data_dir, monkeypatch):
    """KNOWN BEHAVIOUR, pinned: if Yahoo returns nothing, get_prices hands back
    whatever is on disk. The caller receives months-old bars and no indication
    that the refresh failed — this is how a ranking can be computed on prices
    that stopped in July while the screen says 'as of' today."""
    _cache(data_dir, "A.NS", bars(n=5, start="2026-07-20"),
           written=dt.datetime(2026, 7, 24, 18, 0, tzinfo=IST))
    monkeypatch.setattr(stock_prices, "_download", lambda tickers: {})          # Yahoo is down

    out = stock_prices.get_prices(["A.NS"])
    assert out["A.NS"].index[-1].date() == dt.date(2026, 7, 24)


# =============================================================================
# more on the fixed caveats
# =============================================================================

def _age(path, days):
    then = (dt.datetime.now() - dt.timedelta(days=days)).timestamp()
    os.utime(path, (then, then))


def test_a_constituent_list_is_kept_for_thirty_days(data_dir, monkeypatch):
    monkeypatch.setattr(constituents.requests, "get", lambda url, **kw: FakeResponse(NSE_CSV))
    constituents.fetch_constituents("k")
    _age(data_dir / "universes" / "k.csv", 29)
    monkeypatch.setattr(constituents.requests, "get",
                        lambda *a, **k: pytest.fail("29 days old is still current"))

    assert len(constituents.fetch_constituents("k")) == 2
    assert config.CONSTITUENTS_MAX_AGE_DAYS == 30


def test_an_expired_list_is_still_served_when_the_refresh_fails(data_dir, monkeypatch, caplog):
    """A slightly stale universe beats no scan — but it is logged, loudly."""
    monkeypatch.setattr(constituents.requests, "get", lambda url, **kw: FakeResponse(NSE_CSV))
    constituents.fetch_constituents("k")
    _age(data_dir / "universes" / "k.csv", 90)
    monkeypatch.setattr(constituents.requests, "get",
                        lambda url, **kw: FakeResponse(status=503))

    with caplog.at_level("WARNING"):
        df = constituents.fetch_constituents("k")
    assert list(df["symbol"]) == ["SANSERA", "KIRLOSENG"]
    assert "may predate an index reconstitution" in caplog.text


def test_a_forced_refresh_that_fails_is_an_error_not_a_silent_fallback(data_dir, monkeypatch):
    monkeypatch.setattr(constituents.requests, "get", lambda url, **kw: FakeResponse(NSE_CSV))
    constituents.fetch_constituents("k")
    monkeypatch.setattr(constituents.requests, "get",
                        lambda url, **kw: FakeResponse(status=503))
    with pytest.raises(RuntimeError, match="HTTP 503"):
        constituents.fetch_constituents("k", force_refresh=True)


def test_refresh_bypasses_a_fresh_coverage_cache(data_dir, monkeypatch):
    values = iter([3.5, 1.2])
    monkeypatch.setattr(fundamentals, "_fetch_interest_coverage", lambda t: next(values))
    today = dt.date(2026, 11, 14)

    assert fundamentals.get_interest_coverage("X.NS", today) == 3.5
    assert fundamentals.get_interest_coverage("X.NS", today) == 3.5            # cached
    assert fundamentals.get_interest_coverage("X.NS", today, refresh=True) == 1.2
    assert fundamentals.get_interest_coverage("X.NS", today) == 1.2            # and re-cached


def test_a_failed_fetch_leaves_an_older_cached_figure_in_place(data_dir, monkeypatch):
    monkeypatch.setattr(fundamentals, "_fetch_interest_coverage", lambda t: 3.5)
    fundamentals.get_interest_coverage("X.NS", dt.date(2026, 9, 1))

    def boom(t):
        raise ConnectionError("yahoo down")

    monkeypatch.setattr(fundamentals, "_fetch_interest_coverage", boom)
    assert fundamentals.get_interest_coverage("X.NS", dt.date(2026, 11, 14)) is None
    saved = json.loads((data_dir / "fundamentals_cache.json").read_text())
    assert saved["X.NS"] == {"interest_coverage": 3.5, "fetched": "2026-09-01"}


def test_pretax_income_without_an_interest_figure_cannot_give_a_coverage(monkeypatch):
    fake_yf(monkeypatch, income_stmt=stmt(Pretax_Income=[150.0, 1.0]))
    assert fundamentals._fetch_interest_coverage("X.NS") is None


@pytest.mark.parametrize("now,expected", [
    (dt.datetime(2026, 11, 13, 15, 29, tzinfo=IST), dt.date(2026, 11, 6)),    # Fri, before close
    (dt.datetime(2026, 11, 13, 15, 30, tzinfo=IST), dt.date(2026, 11, 13)),   # Fri, at the close
    (dt.datetime(2026, 11, 14, 10, 0, tzinfo=IST), dt.date(2026, 11, 13)),    # Saturday
    (dt.datetime(2026, 11, 16, 9, 0, tzinfo=IST), dt.date(2026, 11, 13)),     # Monday
    (dt.datetime(2026, 11, 19, 16, 0, tzinfo=IST), dt.date(2026, 11, 13)),    # Thursday
])
def test_the_latest_closed_week(now, expected):
    assert market_data._latest_closed_week(now) == expected


def test_when_every_provider_is_stale_the_gate_gets_an_error_not_old_candles(monkeypatch):
    _providers(monkeypatch, manual=_weekly(40, last="2026-07-24"),
               yahoo=_weekly(40, last="2026-10-30"))
    with pytest.raises(RuntimeError) as exc:
        market_data.fetch_weekly_index_data("^CRSLDX", NOW)

    msg = str(exc.value)
    assert "No CURRENT weekly data" in msg and "latest closed week ends 2026-11-13" in msg
    assert "manual:^CRSLDX (stops 2026-07-24)" in msg
    assert "yfinance:^CRSLDX (stops 2026-10-30)" in msg
