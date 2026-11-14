"""Point-in-time feature panel — the score's raw terms, precomputed once.

The engine recomputes every score term for every stock on every week, which
costs ~28 minutes for 400 names over 20 years. But the terms depend only on
price history, never on the formula, so a parameter sweep recomputes identical
numbers hundreds of times.

This module computes them ONCE into (week x symbol) matrices. A formula then
becomes a few array operations — milliseconds instead of minutes — which is
what makes sweeping hundreds of variants, and later training an ML model,
feasible at all.

NO LOOKAHEAD, by construction: every term is a backward-looking rolling window
(`pct_change(k)` reaches back k rows; `rolling(k).std()` covers the k rows
ending at t). Nothing reads a row after t. `test_panel_has_no_lookahead`
asserts this by appending future bars and checking past values are unchanged.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .. import config as app_config
from ..domain import scoring_formula
from . import config as bt_config
from . import price_store, universe

log = logging.getLogger(__name__)


@dataclass
class Panel:
    """Aligned (week x symbol) matrices of everything a selector needs."""

    weeks: list[dt.date]
    symbols: list[str]
    terms: dict[str, np.ndarray]          # token -> (n_weeks, n_symbols)
    close: np.ndarray                     # last close on/before each week
    median_tv_cr: np.ndarray              # 20d median traded value, in crores
    listed_days: np.ndarray               # bars of history available
    dma_50: np.ndarray                    # technical veto inputs (Step 5)
    dma_20: np.ndarray
    rsi_14: np.ndarray
    week_low: np.ndarray                  # lowest low during the FOLLOWING week
    next_open: np.ndarray                 # next session's open (Monday fill)
    sector: list[str]
    sleeve: list[str]
    meta: dict = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return len(self.weeks), len(self.symbols)

    def eligible(self,
                 liquidity_floor_cr: float | None = None,
                 min_listing_days: int | None = None) -> np.ndarray:
        """The same eligibility gate the live scanner applies, vectorized."""
        floor = (app_config.LIQUIDITY_FLOOR_CR if liquidity_floor_cr is None
                 else liquidity_floor_cr)
        min_days = (app_config.MIN_LISTING_DAYS if min_listing_days is None
                    else min_listing_days)
        return (
            np.isfinite(self.close)
            & np.isfinite(self.median_tv_cr) & (self.median_tv_cr >= floor)
            & (self.listed_days >= min_days)
        )


def _stock_terms(daily: pd.DataFrame, week_ends: list[dt.date],
                 median_tv_window: int) -> dict[str, np.ndarray] | None:
    """Every term for one stock, sampled at each week end.

    Vectorized over the full series, then reindexed onto the week grid with
    `method="ffill"` — which carries the last value at or BEFORE each week end,
    never a later one.
    """
    if daily is None or daily.empty:
        return None

    close = daily["close"]
    rets = close.pct_change()
    idx = pd.DatetimeIndex(pd.to_datetime(week_ends))

    out: dict[str, pd.Series] = {}
    for token, (kind, code) in scoring_formula.TERMS.items():
        k = scoring_formula.CODE_DAYS[code]
        if kind == "perf":
            # close[t] / close[t-k] - 1  — reaches strictly backward.
            series = close.pct_change(k)
        else:
            # std of the k daily returns ENDING at t.
            series = rets.rolling(k).std(ddof=1)
        out[token] = series

    # --- technical veto inputs (Step 5), all backward-rolling ---------------
    out["__dma50"] = close.rolling(app_config.DMA_TREND_PERIOD).mean()
    out["__dma20"] = close.rolling(app_config.DMA_EXTENSION_PERIOD).mean()
    delta = close.diff()
    n = app_config.RSI_PERIOD
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    out["__rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    out["__close"] = close
    out["__tv"] = (close * daily["volume"]).rolling(median_tv_window).median() \
        / app_config.RUPEES_PER_CRORE
    # How many bars exist up to each point — the listing-age gate.
    out["__listed"] = pd.Series(np.arange(1, len(close) + 1), index=close.index)

    sampled: dict[str, np.ndarray] = {}
    for name, series in out.items():
        s = series.reindex(series.index.union(idx)).ffill().reindex(idx)
        sampled[name] = s.to_numpy(dtype=float)

    # Execution inputs for the FOLLOWING week. These deliberately look forward —
    # they are what you trade on after the decision, never an input to it, and
    # the simulator only ever reads them for week i+1.
    nxt_open, wk_low = [], []
    lows, opens = daily["low"], daily["open"]
    for a, b in zip(week_ends, list(week_ends[1:]) + [week_ends[-1]]):
        after = opens[opens.index.date > a]
        nxt_open.append(float(after.iloc[0]) if len(after) else np.nan)
        span = lows[(lows.index.date > a) & (lows.index.date <= b)]
        wk_low.append(float(span.min()) if len(span) else np.nan)
    sampled["__next_open"] = np.asarray(nxt_open, dtype=float)
    sampled["__week_low"] = np.asarray(wk_low, dtype=float)
    return sampled


def build(universe_id: str,
          index_id: str = app_config.DEFAULT_INDEX_ID,
          start: dt.date | None = None,
          end: dt.date | None = None,
          median_tv_window: int | None = None,
          progress=None) -> Panel:
    """Build the panel for a universe over the index's weekly calendar."""
    from .engine import BacktestError, resample_weekly

    members = universe.load(universe_id)
    if not members:
        raise BacktestError(f"Universe {universe_id!r} is empty.")

    index_opt = app_config.get_index_option(index_id)
    index_daily = price_store.load(index_opt.ticker)
    if index_daily is None or index_daily.empty:
        raise BacktestError(f"No cached history for {index_opt.ticker}.")

    weekly = resample_weekly(index_daily)
    warmup_end = index_daily.index[0].date() + dt.timedelta(days=bt_config.MIN_WARMUP_DAYS)
    weeks = [d.date() for d in weekly.index if d.date() >= warmup_end]
    if start:
        weeks = [w for w in weeks if w >= start]
    if end:
        weeks = [w for w in weeks if w <= end]
    if not weeks:
        raise BacktestError("No weeks in that range.")

    tv_window = median_tv_window or app_config.MEDIAN_TV_WINDOW
    symbols = [m.symbol for m in members]
    n_w, n_s = len(weeks), len(symbols)

    terms = {t: np.full((n_w, n_s), np.nan) for t in scoring_formula.TERMS}
    blank = lambda: np.full((n_w, n_s), np.nan)  # noqa: E731
    close, tv = blank(), blank()
    dma50, dma20, rsi = blank(), blank(), blank()
    nxt_open, wk_low = blank(), blank()
    listed = np.zeros((n_w, n_s))

    for j, m in enumerate(members):
        if progress and j % 25 == 0:
            progress(j, n_s)
        got = _stock_terms(price_store.load(m.ticker), weeks, tv_window)
        if got is None:
            continue
        for t in terms:
            terms[t][:, j] = got[t]
        close[:, j] = got["__close"]
        tv[:, j] = got["__tv"]
        listed[:, j] = np.nan_to_num(got["__listed"])
        dma50[:, j], dma20[:, j], rsi[:, j] = got["__dma50"], got["__dma20"], got["__rsi"]
        nxt_open[:, j], wk_low[:, j] = got["__next_open"], got["__week_low"]

    return Panel(
        weeks=weeks, symbols=symbols, terms=terms, close=close,
        median_tv_cr=tv, listed_days=listed,
        dma_50=dma50, dma_20=dma20, rsi_14=rsi,
        week_low=wk_low, next_open=nxt_open,
        sector=[m.sector for m in members], sleeve=[m.sleeve for m in members],
        meta={"universe_id": universe_id, "index_id": index_id,
              "index_ticker": index_opt.ticker, "median_tv_window": tv_window},
    )


def score_panel(panel: Panel, compiled: scoring_formula.CompiledFormula) -> np.ndarray:
    """Evaluate a formula across the whole panel at once.

    Returns (n_weeks, n_symbols) of scores; NaN wherever any required term is
    undefined — the same rule `scoring_formula.score` applies per stock.
    """
    values = {t: panel.terms[t] for t in compiled.used_terms}
    with np.errstate(divide="ignore", invalid="ignore"):
        out = _eval_array(compiled.tree.body, values)
    missing = np.zeros(panel.shape, dtype=bool)
    for arr in values.values():
        missing |= ~np.isfinite(arr)
    return np.where(missing, np.nan, out)


def _eval_array(node, values: dict[str, np.ndarray]):
    """Array-valued twin of scoring_formula._eval (same AST, same operators)."""
    import ast

    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.UnaryOp):
        v = _eval_array(node.operand, values)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a = _eval_array(node.left, values)
        b = _eval_array(node.right, values)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b
    raise scoring_formula.FormulaError(f"Unsupported node {type(node).__name__}")


def ranks(scores: np.ndarray, eligible: np.ndarray) -> np.ndarray:
    """Dense 1..n rank per week, best score first. Ineligible/NaN -> 0."""
    out = np.zeros(scores.shape, dtype=int)
    for i in range(scores.shape[0]):
        row = np.where(eligible[i] & np.isfinite(scores[i]), scores[i], -np.inf)
        order = np.argsort(-row, kind="stable")
        keep = np.isfinite(row[order]) & (row[order] > -np.inf)
        out[i, order[keep]] = np.arange(1, keep.sum() + 1)
    return out


# --- caching ---------------------------------------------------------------
# Building a 400x1044 panel takes ~28 minutes. It depends only on cached prices
# and the universe, both of which are immutable for a given run, so it is cached
# on disk and reused by every sweep, every fold and (later) the ML training.

def _cache_path(universe_id: str, index_id: str):
    return bt_config.BACKTEST_DIR / "panels" / f"{universe_id}__{index_id}.npz"


def save(p: Panel) -> None:
    path = _cache_path(p.meta["universe_id"], p.meta["index_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        weeks=np.array([w.isoformat() for w in p.weeks]),
        symbols=np.array(p.symbols), sector=np.array(p.sector),
        sleeve=np.array(p.sleeve), close=p.close, median_tv_cr=p.median_tv_cr,
        listed_days=p.listed_days, dma_50=p.dma_50, dma_20=p.dma_20,
        rsi_14=p.rsi_14, week_low=p.week_low, next_open=p.next_open,
        meta=np.array([repr(p.meta)]),
        **{f"term__{k}": v for k, v in p.terms.items()},
    )


def load_cached(universe_id: str, index_id: str) -> Panel | None:
    path = _cache_path(universe_id, index_id)
    if not path.exists():
        return None
    z = np.load(path, allow_pickle=False)
    terms = {k[len("term__"):]: z[k] for k in z.files if k.startswith("term__")}
    if set(terms) != set(scoring_formula.TERMS):
        log.warning("Cached panel %s has stale terms; rebuilding.", path.name)
        return None
    import ast as _ast
    return Panel(
        weeks=[dt.date.fromisoformat(w) for w in z["weeks"]],
        symbols=list(z["symbols"]), terms=terms, close=z["close"],
        median_tv_cr=z["median_tv_cr"], listed_days=z["listed_days"],
        dma_50=z["dma_50"], dma_20=z["dma_20"], rsi_14=z["rsi_14"],
        week_low=z["week_low"], next_open=z["next_open"],
        sector=list(z["sector"]), sleeve=list(z["sleeve"]),
        meta=_ast.literal_eval(str(z["meta"][0])),
    )


def get(universe_id: str, index_id: str = app_config.DEFAULT_INDEX_ID,
        refresh: bool = False, progress=None) -> Panel:
    """Cached panel for a universe+index, building it only if needed."""
    if not refresh:
        cached = load_cached(universe_id, index_id)
        if cached is not None:
            return cached
    p = build(universe_id, index_id=index_id, progress=progress)
    save(p)
    return p
