"""Benchmarks for the backtest — what the strategy is measured against.

Until Spec v2.3 every run was compared with the REGIME index's price series.
That flattered the strategy twice over: a price index leaves out dividends, and
NIFTY 500 is not the universe the strategy picks from. The fair comparison for
a mid/small-cap momentum book is the NIFTY MIDSMALLCAP 400 TOTAL-RETURN index —
the thing you would own if you simply bought the index fund.

Two kinds of benchmark:

  * "index"  — a ticker already in the backtest price store (the regime
               indices and their ETF proxies). Free, but price-only for true
               indices and short-history for ETFs.
  * "manual" — a CSV you download once and drop into
               `data/backtest/benchmarks/`. NSE blocks automated downloads, so
               total-return history has to arrive this way.

A manual file needs a date column and one value column. Column names are
matched loosely because niftyindices.com exports differ by report.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .. import config as app_config
from . import config as bt_config
from . import price_store

MANUAL_SOURCE_HINT = (
    "niftyindices.com -> Reports -> Historical Data -> pick the index and "
    "'Total Returns Index' -> download the CSV"
)

_DATE_COLUMNS = ("date", "historicaldate")
_VALUE_COLUMNS = (
    "close", "totalreturnsindex", "totalreturnindex", "tri", "value", "nav",
)


class BenchmarkError(RuntimeError):
    """The chosen benchmark cannot be used — unknown id, missing or bad file."""


@dataclass(frozen=True)
class BenchmarkOption:
    id: str
    label: str
    source: str             # "index" | "manual"
    ref: str                # price-store ticker, or the manual file name
    total_return: bool      # dividends included?
    note: str = ""


MANUAL_OPTIONS: list[BenchmarkOption] = [
    BenchmarkOption(
        "midsmall400_tri", "NIFTY MIDSMALLCAP 400 TRI", "manual", "midsmall400_tri.csv",
        True, "the fair comparison: the index fund you would otherwise hold"),
    BenchmarkOption(
        "nifty500_tri", "NIFTY 500 TRI", "manual", "nifty500_tri.csv",
        True, "broad market with dividends"),
]


def benchmarks_dir() -> Path:
    # Derived on every call so a test (or a relocated data tree) that repoints
    # BACKTEST_DIR is honoured without a second path to keep in step.
    return bt_config.BACKTEST_DIR / "benchmarks"


def _from_index(opt: app_config.IndexOption) -> BenchmarkOption:
    etf = opt.kind == "etf_proxy"
    return BenchmarkOption(
        id=opt.id, label=opt.label, source="index", ref=opt.ticker, total_return=etf,
        note=("ETF — dividends are reinvested inside the fund, net of its expenses"
              if etf else "PRICE index — dividends are excluded"),
    )


def options() -> list[BenchmarkOption]:
    """Every selectable benchmark: total-return files first, then the indices."""
    return MANUAL_OPTIONS + [_from_index(o) for o in app_config.INDEX_OPTIONS]


def get_option(benchmark_id: str | None,
               regime_index: app_config.IndexOption) -> BenchmarkOption:
    """Resolve an id. None keeps the historical behaviour: the regime index."""
    if not benchmark_id:
        return _from_index(regime_index)
    for opt in options():
        if opt.id == benchmark_id:
            return opt
    raise BenchmarkError(f"Unknown benchmark {benchmark_id!r}.")


def _normalise(name: object) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def load_manual(path: Path) -> pd.Series:
    """Read a downloaded benchmark CSV into a date-indexed close series."""
    if not path.exists():
        raise BenchmarkError(
            f"Benchmark file {path.name} is not in {path.parent}. Download it from "
            f"{MANUAL_SOURCE_HINT}, then save it under that name.")

    df = pd.read_csv(path)
    by_norm = {_normalise(c): c for c in df.columns}
    date_col = next((by_norm[c] for c in _DATE_COLUMNS if c in by_norm), None)
    value_col = next((by_norm[c] for c in _VALUE_COLUMNS if c in by_norm), None)
    if date_col is None or value_col is None:
        raise BenchmarkError(
            f"{path.name} needs a date column and a value column (close / Total Returns "
            f"Index); found {list(df.columns)}.")

    # ISO dates are unambiguous and read as such. Everything else is day-first,
    # which is what niftyindices exports use ("01 Jan 2020", "01-01-2020") —
    # letting pandas guess would silently turn 5 January into 1 May.
    raw = df[date_col].astype(str).str.strip()
    iso = pd.to_datetime(raw, format="%Y-%m-%d", errors="coerce")
    day_first = pd.to_datetime(raw, errors="coerce", dayfirst=True, format="mixed")
    dates = iso.where(iso.notna(), day_first)
    values = pd.to_numeric(
        df[value_col].astype(str).str.replace(",", "", regex=False), errors="coerce")
    series = pd.Series(values.to_numpy(), index=dates, name="close")
    series = series[series.index.notna()].dropna().sort_index()
    series = series[~series.index.duplicated(keep="last")]
    if series.empty:
        raise BenchmarkError(f"{path.name} has no usable rows.")
    return series.astype(float)


def series(opt: BenchmarkOption) -> pd.Series:
    """The daily close series for a benchmark. Raises BenchmarkError if absent."""
    if opt.source == "manual":
        return load_manual(benchmarks_dir() / opt.ref)

    df = price_store.load(opt.ref)
    if df is None or df.empty:
        raise BenchmarkError(
            f"No cached history for the benchmark {opt.label} ({opt.ref}). "
            "Fetch prices first.")
    return df["close"].astype(float)


def coverage(opt: BenchmarkOption) -> tuple[str, str] | None:
    """(first, last) ISO dates available for a benchmark, or None if unusable."""
    try:
        s = series(opt)
    except BenchmarkError:
        return None
    return s.index[0].date().isoformat(), s.index[-1].date().isoformat()
