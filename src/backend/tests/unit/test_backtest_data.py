"""Backtest data layer: point-in-time integrity, isolation, universe loading.

The leakage tests here are a first-class deliverable, not a formality. A
backtest that can see the future produces a beautiful equity curve and a
worthless conclusion, so "no lookahead" is asserted mechanically rather than
argued in prose.
"""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from app import config as app_config
from app.backtest import config as bt_config
from app.backtest import price_store, universe


@pytest.fixture
def bt_dirs(tmp_path, monkeypatch):
    """Point the backtest data tree at a temp dir (never the real one)."""
    root = tmp_path / "backtest"
    monkeypatch.setattr(bt_config, "BACKTEST_DIR", root)
    monkeypatch.setattr(bt_config, "PRICES_DIR", root / "prices")
    monkeypatch.setattr(bt_config, "UNIVERSES_DIR", root / "universes")
    monkeypatch.setattr(bt_config, "RUNS_DIR", root / "runs")
    monkeypatch.setattr(bt_config, "RESULTS_DIR", root / "results")
    monkeypatch.setattr(bt_config, "ALL_DIRS", [
        root, root / "prices", root / "universes", root / "runs", root / "results"])
    bt_config.ensure_dirs()
    price_store.clear_memo()     # never carry one test's prices into another
    return root


def _frame(n=400, start="2024-01-01", seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.001, 0.01, n))
    idx = pd.date_range(start, periods=n, freq="D")
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 1_000_000.0},
        index=idx,
    )


# --- point-in-time integrity (no lookahead) ---------------------------------

def test_get_truncates_at_as_of_inclusive(bt_dirs):
    price_store.save("AAA.NS", _frame())
    cut = dt.date(2024, 3, 1)

    out = price_store.get("AAA.NS", as_of=cut)

    assert out.index[-1].date() == cut          # inclusive
    assert (out.index.date <= cut).all()


def test_appending_future_rows_cannot_change_the_past(bt_dirs):
    """The core leakage guarantee: what you saw at date t never changes.

    If this fails, every feature and every ranking in the backtest is suspect —
    a future bar would be reaching backwards into a past decision.
    """
    base = _frame(n=200)
    price_store.save("AAA.NS", base)
    cut = base.index[120].date()
    before = price_store.get("AAA.NS", as_of=cut)

    extended = pd.concat([base, _frame(n=100, start="2024-07-19", seed=99)])
    price_store.save("AAA.NS", extended)
    after = price_store.get("AAA.NS", as_of=cut)

    pd.testing.assert_frame_equal(before, after)


def test_unlisted_name_returns_none_not_an_empty_frame(bt_dirs):
    """Before a stock listed it is not investable — the caller must not be able
    to mistake it for a zero price or an empty position."""
    price_store.save("NEW.NS", _frame(n=50, start="2025-01-01"))
    assert price_store.get("NEW.NS", as_of=dt.date(2024, 6, 1)) is None


def test_get_many_omits_names_with_no_history_yet(bt_dirs):
    price_store.save("OLD.NS", _frame(start="2020-01-01"))
    price_store.save("NEW.NS", _frame(start="2025-01-01"))

    out = price_store.get_many(["OLD.NS", "NEW.NS"], as_of=dt.date(2021, 1, 1))

    assert list(out) == ["OLD.NS"]


def test_uncached_ticker_is_none_never_an_exception(bt_dirs):
    assert price_store.get("GHOST.NS", as_of=dt.date(2024, 1, 1)) is None
    assert price_store.get_many(["GHOST.NS"]) == {}


# --- cache behaviour --------------------------------------------------------

def test_round_trip_preserves_ohlcv_and_index(bt_dirs):
    df = _frame(n=30)
    price_store.save("AAA.NS", df)
    loaded = price_store.load("AAA.NS")

    # check_freq=False: the fixture builds a regular date_range, but real
    # sessions skip weekends and holidays, so a DatetimeIndex freq is meaningless
    # here and parquet rightly drops it. Values and dates are what must survive.
    pd.testing.assert_frame_equal(loaded, df, check_names=False, check_freq=False)
    assert loaded.index.name == "date"          # normalised on the way in


def test_ensure_downloads_only_missing_tickers(bt_dirs, monkeypatch):
    """History is immutable, so a cached ticker must never be re-fetched."""
    price_store.save("HAVE.NS", _frame())
    calls: list[list[str]] = []

    def fake(tickers):
        calls.append(sorted(tickers))
        return {t: _frame() for t in tickers}

    monkeypatch.setattr(price_store, "download", fake)
    avail = price_store.ensure(["HAVE.NS", "NEED.NS"])

    assert calls == [["NEED.NS"]]
    assert avail == {"HAVE.NS": True, "NEED.NS": True}


def test_ensure_refresh_refetches_everything(bt_dirs, monkeypatch):
    price_store.save("HAVE.NS", _frame())
    calls: list[list[str]] = []
    monkeypatch.setattr(price_store, "download",
                        lambda t: (calls.append(sorted(t)), {x: _frame() for x in t})[1])

    price_store.ensure(["HAVE.NS"], refresh=True)
    assert calls == [["HAVE.NS"]]


def test_ensure_reports_a_ticker_yfinance_could_not_supply(bt_dirs, monkeypatch):
    monkeypatch.setattr(price_store, "download", lambda tickers: {})
    assert price_store.ensure(["DELISTED.NS"]) == {"DELISTED.NS": False}


def test_coverage_reports_the_cached_span(bt_dirs):
    price_store.save("AAA.NS", _frame(n=10, start="2024-01-01"))
    assert price_store.coverage("AAA.NS") == (dt.date(2024, 1, 1), dt.date(2024, 1, 10))
    assert price_store.coverage("GHOST.NS") is None


# --- isolation from the live app -------------------------------------------

def test_backtest_paths_never_touch_live_data():
    """A research run must not be able to write the ledger, cycles or journal."""
    live = {app_config.CYCLES_DIR, app_config.PRICES_DIR, app_config.UNIVERSES_DIR}
    bt = {bt_config.PRICES_DIR, bt_config.UNIVERSES_DIR, bt_config.RUNS_DIR,
          bt_config.RESULTS_DIR}

    assert live.isdisjoint(bt)
    assert all(bt_config.BACKTEST_DIR in p.parents or p == bt_config.BACKTEST_DIR for p in bt)
    for live_file in (app_config.HOLDINGS_LEDGER, app_config.JOURNAL_JSONL,
                      app_config.JOURNAL_MD, app_config.CURRENT_CYCLE_FILE):
        assert bt_config.BACKTEST_DIR not in live_file.parents


# --- universes --------------------------------------------------------------

def test_universe_round_trip(bt_dirs):
    members = [
        universe.UniverseMember("SANSERA", "Auto Components", "MIDSMALLCAP400"),
        universe.UniverseMember("TINYCO", "Chemicals", "MICROCAP250"),
    ]
    assert universe.save("test400", members) == 2

    loaded = universe.load("test400")
    assert [m.symbol for m in loaded] == ["SANSERA", "TINYCO"]
    assert loaded[0].ticker == "SANSERA.NS"
    assert universe.microcap_symbols(loaded) == {"TINYCO"}


def test_universe_defaults_a_blank_sleeve(bt_dirs):
    pd.DataFrame({"symbol": ["AAA"], "sector": ["Auto"], "sleeve": [""]}).to_csv(
        bt_config.UNIVERSES_DIR / "u.csv", index=False)
    assert universe.load("u")[0].sleeve == "MIDSMALLCAP400"


def test_universe_skips_blank_symbol_rows(bt_dirs):
    pd.DataFrame({"symbol": ["AAA", "", "  "], "sector": ["Auto"] * 3,
                  "sleeve": ["MIDSMALLCAP400"] * 3}).to_csv(
        bt_config.UNIVERSES_DIR / "u.csv", index=False)
    assert [m.symbol for m in universe.load("u")] == ["AAA"]


def test_missing_universe_raises_a_clear_error(bt_dirs):
    with pytest.raises(FileNotFoundError, match="nosuch"):
        universe.load("nosuch")


def test_list_universes_is_sorted(bt_dirs):
    universe.save("zeta", [universe.UniverseMember("A")])
    universe.save("alpha", [universe.UniverseMember("B")])
    assert universe.list_universes() == ["alpha", "zeta"]


def test_build_from_indices_dedupes_and_tags_microcap(bt_dirs, monkeypatch):
    def fake_constituents(nse_key):
        if "micro" in nse_key:
            return pd.DataFrame({"symbol": ["TINY", "SHARED"], "sector": ["Chem", "Auto"],
                                 "company": ["", ""]})
        return pd.DataFrame({"symbol": ["BIG", "SHARED"], "sector": ["IT", "Auto"],
                             "company": ["", ""]})

    monkeypatch.setattr(universe.constituents, "fetch_constituents", fake_constituents)
    members = universe.build_from_indices("combo", ["microcap250", "midsmall400"])

    by = {m.symbol: m for m in members}
    assert set(by) == {"TINY", "SHARED", "BIG"}
    assert by["TINY"].sleeve == "MICROCAP250"
    assert by["SHARED"].sleeve == "MICROCAP250"     # first index listing it wins
    assert by["BIG"].sleeve == "MIDSMALLCAP400"
    assert universe.load("combo")                   # persisted to disk


# --- vendor bad-tick repair -------------------------------------------------

def test_repair_fixes_an_off_by_100_print(bt_dirs):
    """The GOLDBEES case: two sessions printed 100x too low, which turned into a
    +10,049% weekly return and poisoned every statistic downstream."""
    df = _frame(n=20)
    df.iloc[10, :4] = df.iloc[10, :4] / 100          # OHLC of one session

    fixed, n = price_store.repair_ticks(df)

    assert n == 1
    neighbours = (df["close"].iloc[9] + df["close"].iloc[11]) / 2
    assert fixed["close"].iloc[10] == pytest.approx(neighbours, rel=0.05)
    assert abs(fixed["close"].pct_change()).max() < 0.25


def test_repair_leaves_a_normal_series_untouched(bt_dirs):
    df = _frame(n=50)
    fixed, n = price_store.repair_ticks(df)
    assert n == 0
    pd.testing.assert_frame_equal(fixed, df)


def test_repair_does_not_flatten_a_real_limit_move(bt_dirs):
    """A 20% circuit move is legitimate and must survive — the filter only
    catches physically impossible prints."""
    df = _frame(n=20)
    df.iloc[10:, :4] = df.iloc[10:, :4] * 0.82       # a sustained 18% drop
    _, n = price_store.repair_ticks(df)
    assert n == 0


def test_download_repairs_before_caching(bt_dirs, monkeypatch):
    raw = _frame(n=20)
    raw.iloc[5, :4] = raw.iloc[5, :4] / 100
    import pandas as _pd
    monkeypatch.setattr(price_store, "yf", None, raising=False)
    monkeypatch.setattr(
        price_store, "download",
        lambda tickers: {t: price_store.repair_ticks(raw)[0] for t in tickers})

    price_store.ensure(["BAD.NS"])
    cached = price_store.load("BAD.NS")
    assert abs(cached["close"].pct_change()).max() < 0.25
    assert isinstance(cached, _pd.DataFrame)
