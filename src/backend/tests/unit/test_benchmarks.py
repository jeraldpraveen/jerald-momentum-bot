"""Backtest benchmarks — registry, the manual total-return loader, coverage."""

import numpy as np
import pandas as pd
import pytest

from app import config as app_config
from app.backtest import benchmarks, price_store
from app.backtest.benchmarks import BenchmarkError, BenchmarkOption
from tests.unit.test_backtest_data import bt_dirs  # noqa: F401

NIFTY500 = app_config.get_index_option("nifty500")


def _prices(n=30, start="2024-01-01"):
    close = np.linspace(100.0, 130.0, n)
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1.0},
        index=pd.bdate_range(start, periods=n))


def _write(root, name, text):
    d = root / "benchmarks"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")
    return d / name


# --- registry ---------------------------------------------------------------

def test_total_return_files_are_listed_before_the_indices():
    opts = benchmarks.options()
    assert [o.id for o in opts[:2]] == ["midsmall400_tri", "nifty500_tri"]
    assert all(o.source == "manual" and o.total_return for o in opts[:2])
    assert {o.id for o in opts[2:]} == {o.id for o in app_config.INDEX_OPTIONS}


def test_a_true_index_is_flagged_as_price_only():
    opt = next(o for o in benchmarks.options() if o.id == "nifty500")
    assert opt.source == "index" and opt.ref == "^CRSLDX"
    assert opt.total_return is False and "PRICE index" in opt.note


def test_an_etf_proxy_counts_as_total_return():
    opt = next(o for o in benchmarks.options() if o.id == "midsmall400")
    assert opt.total_return is True and "ETF" in opt.note


def test_no_benchmark_id_means_the_regime_index():
    opt = benchmarks.get_option(None, NIFTY500)
    assert (opt.id, opt.ref, opt.source) == ("nifty500", "^CRSLDX", "index")
    assert benchmarks.get_option("", NIFTY500) == opt


def test_a_named_benchmark_is_resolved():
    opt = benchmarks.get_option("midsmall400_tri", NIFTY500)
    assert opt.label == "NIFTY MIDSMALLCAP 400 TRI" and opt.ref == "midsmall400_tri.csv"


def test_an_unknown_benchmark_is_a_clear_error():
    with pytest.raises(BenchmarkError, match="Unknown benchmark 'nope'"):
        benchmarks.get_option("nope", NIFTY500)


def test_benchmarks_dir_follows_the_backtest_root(bt_dirs):  # noqa: F811
    assert benchmarks.benchmarks_dir() == bt_dirs / "benchmarks"


# --- manual loader ----------------------------------------------------------

def test_loads_a_plain_date_close_file(bt_dirs):  # noqa: F811
    path = _write(bt_dirs, "x.csv", "date,close\n2024-01-02,100.5\n2024-01-03,101\n")
    s = benchmarks.load_manual(path)
    assert list(s) == [100.5, 101.0]
    assert s.index[0] == pd.Timestamp("2024-01-02") and s.name == "close"


def test_loads_a_niftyindices_total_returns_export(bt_dirs):  # noqa: F811
    """Day-first dates, a spaced header, thousands separators, newest row first."""
    path = _write(bt_dirs, "x.csv", (
        '"Index Name","Date","Total Returns Index"\n'
        '"NIFTY MIDSMALLCAP 400","03 Jan 2024","20,150.50"\n'
        '"NIFTY MIDSMALLCAP 400","02 Jan 2024","20,000.00"\n'))
    s = benchmarks.load_manual(path)
    assert list(s) == [20000.0, 20150.5]                       # sorted oldest first
    assert s.index[-1] == pd.Timestamp("2024-01-03")


def test_numeric_day_first_dates_are_not_read_as_month_first(bt_dirs):  # noqa: F811
    path = _write(bt_dirs, "x.csv", "Date,Close\n05-01-2024,100\n06-01-2024,101\n")
    s = benchmarks.load_manual(path)
    assert s.index[0] == pd.Timestamp("2024-01-05")            # 5 January, not 1 May


def test_bad_rows_and_duplicate_dates_are_dropped(bt_dirs):  # noqa: F811
    path = _write(bt_dirs, "x.csv", (
        "date,close\n"
        "2024-01-02,100\n"
        "not-a-date,101\n"
        "2024-01-03,-\n"
        "2024-01-04,102\n"
        "2024-01-04,103\n"))
    s = benchmarks.load_manual(path)
    assert list(s) == [100.0, 103.0]                           # last duplicate wins


def test_a_missing_file_says_where_to_get_it(bt_dirs):  # noqa: F811
    with pytest.raises(BenchmarkError) as exc:
        benchmarks.load_manual(bt_dirs / "benchmarks" / "midsmall400_tri.csv")
    msg = str(exc.value)
    assert "midsmall400_tri.csv" in msg and "niftyindices.com" in msg


@pytest.mark.parametrize("header", ["when,close", "date,price"])
def test_a_file_without_the_needed_columns_is_rejected(bt_dirs, header):  # noqa: F811
    path = _write(bt_dirs, "x.csv", f"{header}\n2024-01-02,100\n")
    with pytest.raises(BenchmarkError, match="needs a date column and a value column"):
        benchmarks.load_manual(path)


def test_a_file_with_no_usable_rows_is_rejected(bt_dirs):  # noqa: F811
    path = _write(bt_dirs, "x.csv", "date,close\nnope,nope\n")
    with pytest.raises(BenchmarkError, match="no usable rows"):
        benchmarks.load_manual(path)


# --- series + coverage ------------------------------------------------------

def test_series_reads_a_manual_benchmark_from_the_benchmarks_dir(bt_dirs):  # noqa: F811
    _write(bt_dirs, "midsmall400_tri.csv", "date,tri\n2024-01-02,10\n2024-01-03,11\n")
    opt = benchmarks.get_option("midsmall400_tri", NIFTY500)
    assert list(benchmarks.series(opt)) == [10.0, 11.0]


def test_series_reads_an_index_benchmark_from_the_price_store(bt_dirs):  # noqa: F811
    price_store.save("^CRSLDX", _prices())
    s = benchmarks.series(benchmarks.get_option(None, NIFTY500))
    assert len(s) == 30 and s.iloc[0] == 100.0


def test_an_uncached_index_benchmark_is_a_clear_error(bt_dirs):  # noqa: F811
    with pytest.raises(BenchmarkError, match="No cached history for the benchmark"):
        benchmarks.series(benchmarks.get_option(None, NIFTY500))


def test_an_empty_cached_index_is_a_clear_error(bt_dirs, monkeypatch):  # noqa: F811
    monkeypatch.setattr(price_store, "load", lambda t: _prices().iloc[0:0])
    with pytest.raises(BenchmarkError, match="No cached history"):
        benchmarks.series(BenchmarkOption("x", "X", "index", "X.NS", False))


def test_coverage_reports_first_and_last_session(bt_dirs):  # noqa: F811
    price_store.save("^CRSLDX", _prices(start="2024-01-01"))
    first, last = benchmarks.coverage(benchmarks.get_option(None, NIFTY500))
    assert first == "2024-01-01" and last > first


def test_coverage_is_none_when_the_benchmark_is_unavailable(bt_dirs):  # noqa: F811
    assert benchmarks.coverage(benchmarks.get_option("nifty500_tri", NIFTY500)) is None
