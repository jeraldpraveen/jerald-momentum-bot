"""The backtest engine — walks history one Friday at a time.

Every decision comes from `app.domain.strategy`, the same functions the live
Friday ritual calls. The engine only supplies point-in-time data and simulates
execution, so a change to a rule automatically changes both.

The week, mirroring the real ritual:
  Friday close  — regime gate; if GREEN: score, audit holdings, technical veto,
                  risk caps. Nothing is executed on Friday.
  Monday open   — sells first (rank exits, RED liquidation), then buys with the
                  freed cash. Stops go on at fill x 0.80.
  Mon-Fri       — if a daily LOW pierces a stop, the position is closed. The
                  fill is min(stop, that day's open), so a gap-down fills at the
                  gap, never optimistically at the stop.

SCOPE BOUNDARY — the fundamental veto (pledging, interest coverage, pending
actions) is NOT simulated: those values are not obtainable historically. The
Rulebook's own data-safeguard says an unverifiable check passes with a flag, so
the backtest applies exactly that. Results therefore reflect the price-and-
liquidity half of the strategy, and this is reported with every run.
"""

from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

from .. import config as app_config
from ..domain import scoring_formula
from ..domain import strategy
from ..schemas.scanner import ScannerFilters
from . import config as bt_config
from . import metrics as metrics_mod
from . import price_store, universe
from .costs import CostModel
from .portfolio import Portfolio

log = logging.getLogger(__name__)


def resample_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Daily bars -> W-FRI candles (same aggregation as the live gate)."""
    weekly = daily.resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    weekly.index.name = "week_end"
    return weekly


class BacktestError(RuntimeError):
    """A run cannot proceed — missing data, empty universe, bad dates."""


def _next_session(index_days: list[dt.date], after: dt.date) -> dt.date | None:
    """The first trading day strictly after `after` (the Monday open)."""
    for d in index_days:
        if d > after:
            return d
    return None


def _open_price(df: pd.DataFrame | None, on: dt.date) -> float | None:
    if df is None or df.empty:
        return None
    rows = df[df.index.date == on]
    return float(rows["open"].iloc[0]) if not rows.empty else None


def run_backtest(
    *,
    universe_id: str,
    index_id: str = app_config.DEFAULT_INDEX_ID,
    filter_id: str = app_config.DEFAULT_FILTER_ID,
    formula: str = scoring_formula.DEFAULT_FORMULA,
    start: dt.date | None = None,
    end: dt.date | None = None,
    starting_capital: float = bt_config.STARTING_CAPITAL,
    position_weight_pct: float = bt_config.POSITION_WEIGHT_PCT,
    stop_multiplier: float = bt_config.STOP_MULTIPLIER,
    cost_bps: float = bt_config.COST_BPS,
    slippage_bps: float = bt_config.SLIPPAGE_BPS,
    rank_exit_threshold: int | None = None,
    veto_rank_window: int | None = None,
    alt_asset_id: str | None = None,
    alt_allocation_pct: float = 100.0,
    cash_yield_pct: float = 0.0,
    progress=None,
) -> dict:
    """Simulate the strategy week by week. Returns a result dict (JSON-ready)."""
    members = universe.load(universe_id)
    if not members:
        raise BacktestError(f"Universe {universe_id!r} is empty.")

    index_opt = app_config.get_index_option(index_id)
    filt = app_config.get_filter_option(filter_id)
    compiled = scoring_formula.compile_formula(formula)
    costs = CostModel(cost_bps=cost_bps, slippage_bps=slippage_bps)
    rank_exit_threshold = rank_exit_threshold or app_config.RANK_EXIT_THRESHOLD
    veto_rank_window = veto_rank_window or app_config.VETO_RANK_WINDOW

    alt_opt = app_config.get_alt_asset_option(alt_asset_id)
    alt_ticker = alt_opt.ticker if alt_opt else None

    index_daily = price_store.load(index_opt.ticker)
    if index_daily is None or index_daily.empty:
        raise BacktestError(
            f"No cached history for the index {index_opt.ticker}. Fetch prices first.")

    index_weekly = resample_weekly(index_daily)
    index_days = [d.date() for d in index_daily.index]

    # Warm-up: the score needs a year of candles before the first decision.
    warmup_end = index_days[0] + dt.timedelta(days=bt_config.MIN_WARMUP_DAYS)
    fridays = [d.date() for d in index_weekly.index]
    fridays = [f for f in fridays if f >= warmup_end]
    if start:
        fridays = [f for f in fridays if f >= start]
    if end:
        fridays = [f for f in fridays if f <= end]
    if not fridays:
        raise BacktestError(
            "No tradable weeks in that range — try an earlier start, or fetch more history.")

    # Silently starting later than asked is how a "2010-2026" run quietly became
    # a 2025 run. Record the clip so it is reported with the result.
    # Only the WARM-UP floor counts as a clip. A start of Wednesday the 1st
    # naturally becomes Friday the 3rd — that is the weekly rebalance grid, not
    # a data limitation, and warning about it would cry wolf.
    clipped = None
    if start and start < warmup_end:
        clipped = (
            f"Requested start {start.isoformat()} was moved to {fridays[0].isoformat()}: "
            f"the regime index {index_opt.label} ({index_opt.ticker}) has data only from "
            f"{index_days[0].isoformat()}, plus a {bt_config.MIN_WARMUP_DAYS}-day warm-up the "
            "score needs. Choose an index with longer history (NIFTY 500 goes back to 2005) "
            "to test earlier periods."
        )
        log.warning(clipped)

    tickers = [m.ticker for m in members]
    sector_of = {m.symbol: m.sector for m in members}
    sleeve_of = {m.symbol: m.sleeve for m in members}
    micro = universe.microcap_symbols(members)

    weekly_cash_yield = (1 + cash_yield_pct / 100) ** (1 / metrics_mod.WEEKS_PER_YEAR) - 1
    pf = Portfolio(cash=starting_capital, costs=costs)
    equity_rows: list[dict] = []
    weekly_log: list[dict] = []
    exposure: list[bool] = []
    traded_value = 0.0

    for i, friday in enumerate(fridays):
        if progress and i % 10 == 0:
            progress(i, len(fridays), friday)

        # --- Friday close: decide -----------------------------------------
        weekly_slice = index_weekly[index_weekly.index.date <= friday]
        if len(weekly_slice) < 2:
            continue
        regime, line_label, line_value = strategy.decide_regime(weekly_slice, filt)

        prices = price_store.get_many(tickers, as_of=friday)
        closes = {t[:-3]: float(df["close"].iloc[-1]) for t, df in prices.items()}

        exits: list[str] = []
        buys: list[strategy.RankedName] = []
        candidates = []

        if regime == strategy.RED:
            exits = pf.symbols()
        else:
            candidates = strategy.build_candidates(
                [(m.ticker, m.sector, m.symbol) for m in members], prices, compiled,
                ScannerFilters(),
            )
            held = [strategy.Position(s, sector_of.get(s, ""), sleeve_of.get(s, ""))
                    for s in pf.symbols()]
            audit = strategy.audit_holdings(held, candidates,
                                            rank_threshold=rank_exit_threshold)
            exits = [a.symbol for a in audit if a.exit_flagged]

            # Ranks 1..N only, minus what we already hold and what we're exiting.
            keep = {a.symbol for a in audit if not a.exit_flagged}
            shortlist = [
                strategy.RankedName(c.symbol, c.rank, c.sector)
                for c in candidates
                if c.rank is not None and c.rank <= veto_rank_window
                and c.symbol not in keep and c.symbol not in exits
            ]
            survivors = [r for r in strategy.evaluate_technical(shortlist, prices)
                         if not r.struck]

            remaining = [strategy.Position(s, sector_of.get(s, ""), sleeve_of.get(s, ""))
                         for s in keep]
            caps = strategy.apply_caps(
                [strategy.RankedName(s.symbol, s.rank, s.sector) for s in survivors],
                remaining, micro,
            )
            buys = caps.buys

        # --- Monday open: execute -----------------------------------------
        monday = _next_session(index_days, friday)
        if monday is not None:
            full = {t: price_store.get(t, as_of=monday) for t in tickers}
            opens = {t[:-3]: _open_price(df, monday) for t, df in full.items()}

            alt_open = _open_price(price_store.get(alt_ticker, as_of=monday), monday)                 if alt_ticker else None

            for sym in exits:                                   # sells first
                px = opens.get(sym) or closes.get(sym)
                if px:
                    t = pf.sell(sym, px, monday,
                                reason="regime_red" if regime == strategy.RED else "rank_exit")
                    if t:
                        traded_value += t.value

            # The alt asset is a parking spot for RED weeks: park after the
            # equity sells settle, unpark before the GREEN buys need the cash.
            if regime == strategy.RED and alt_ticker and alt_open:
                target = pf.equity({**closes}) * alt_allocation_pct / 100
                held_alt = pf.alt.qty * alt_open if pf.alt else 0.0
                if target - held_alt > alt_open:
                    t = pf.buy_alt(alt_ticker, alt_open, target - held_alt, monday)
                    if t:
                        traded_value += t.value
            elif regime != strategy.RED and pf.alt is not None and alt_open:
                t = pf.sell_alt(alt_open, monday)
                if t:
                    traded_value += t.value

            if regime != strategy.RED:
                budget_each = pf.equity({**closes}) * position_weight_pct / 100
                for b in buys:
                    px = opens.get(b.symbol) or closes.get(b.symbol)
                    if not px:
                        continue
                    t = pf.buy(b.symbol, px, min(budget_each, pf.cash), monday,
                               stop_multiplier=stop_multiplier,
                               sector=sector_of.get(b.symbol, ""),
                               sleeve=sleeve_of.get(b.symbol, ""),
                               reason=f"rank_{b.rank}")
                    if t:
                        traded_value += t.value

        # --- through the week: stops --------------------------------------
        next_friday = fridays[i + 1] if i + 1 < len(fridays) else None
        stop_hits = _apply_stops(pf, tickers, monday, next_friday, traded_value)
        traded_value = stop_hits["traded_value"]

        # --- interest on idle cash (a fixed-yield parking baseline) --------
        if weekly_cash_yield and pf.cash > 0:
            pf.cash *= 1 + weekly_cash_yield

        # --- mark to market at the next Friday close ----------------------
        mark_date = next_friday or friday
        mark_prices = {
            t[:-3]: float(df["close"].iloc[-1])
            for t, df in price_store.get_many(tickers, as_of=mark_date).items()
        }
        if alt_ticker:                       # value the parking asset as well
            alt_df = price_store.get(alt_ticker, as_of=mark_date)
            if alt_df is not None and not alt_df.empty:
                mark_prices[alt_ticker] = float(alt_df["close"].iloc[-1])
        eq = pf.equity(mark_prices)
        equity_rows.append({"date": mark_date.isoformat(), "equity": round(eq, 2),
                            "cash": round(pf.cash, 2), "positions": len(pf.holdings),
                            "regime": regime,
                            "alt_value": round(pf.alt.qty * mark_prices.get(alt_ticker, 0.0), 2)
                            if pf.alt and alt_ticker else 0.0})
        exposure.append(bool(pf.holdings))
        weekly_log.append({
            "week_end": friday.isoformat(), "regime": regime,
            "line_label": line_label, "line_value": line_value,
            "exits": exits, "buys": [b.symbol for b in buys],
            "stops_hit": stop_hits["symbols"],
            "held": pf.symbols(), "equity": round(eq, 2),
            "eligible": sum(1 for c in candidates if c.eligible),
        })

    return _assemble(
        pf, equity_rows, weekly_log, exposure, traded_value,
        index_daily=index_daily, fridays=fridays, starting_capital=starting_capital,
        universe_id=universe_id, index_opt=index_opt, filt=filt, formula=formula,
        alt_opt=alt_opt, alt_allocation_pct=alt_allocation_pct, clipped=clipped,
        cash_yield_pct=cash_yield_pct,
        requested_start=start,
        position_weight_pct=position_weight_pct, stop_multiplier=stop_multiplier,
        cost_bps=cost_bps, slippage_bps=slippage_bps, members=members,
    )


def _apply_stops(pf: Portfolio, tickers: list[str], start: dt.date | None,
                 end: dt.date | None, traded_value: float) -> dict:
    """Close any position whose daily LOW pierced its stop during the week.

    Fill = min(stop, that day's open). A gap-down opens below the stop, and the
    order fills at the gap — assuming the stop price would be honoured is the
    single most common way a backtest flatters itself.
    """
    hits: list[str] = []
    if start is None or end is None:
        return {"symbols": hits, "traded_value": traded_value}

    for sym in pf.symbols():
        h = pf.holdings[sym]
        df = price_store.get(f"{sym}.NS", as_of=end)
        if df is None:
            continue
        window = df[(df.index.date > start) & (df.index.date <= end)]
        for ts, row in window.iterrows():
            if float(row["low"]) <= h.stop_price:
                fill = min(h.stop_price, float(row["open"]))
                t = pf.sell(sym, fill, ts.date(), reason="stop_hit", fill_price=fill)
                if t:
                    traded_value += t.value
                    hits.append(sym)
                break
    return {"symbols": hits, "traded_value": traded_value}


def _assemble(pf, equity_rows, weekly_log, exposure, traded_value, *, index_daily,
              fridays, starting_capital, universe_id, index_opt, filt, formula,
              position_weight_pct, stop_multiplier, cost_bps, slippage_bps, members,
              alt_opt=None, alt_allocation_pct=100.0, clipped=None,
              requested_start=None, cash_yield_pct=0.0) -> dict:
    curve = pd.Series(
        [r["equity"] for r in equity_rows],
        index=pd.to_datetime([r["date"] for r in equity_rows]),
    )
    m = metrics_mod.compute(curve, exposure)

    bench_daily = index_daily[(index_daily.index.date >= fridays[0])
                              & (index_daily.index.date <= fridays[-1])]
    bench_weekly = resample_weekly(bench_daily)["close"] if not bench_daily.empty else pd.Series(dtype=float)
    bench_curve = metrics_mod.buy_and_hold(bench_weekly, starting_capital)
    bench = metrics_mod.compute(bench_curve)

    mean_equity = float(curve.mean()) if len(curve) else 0.0
    return {
        "config": {
            "universe_id": universe_id, "universe_size": len(members),
            "index_id": index_opt.id, "index_label": index_opt.label,
            "index_ticker": index_opt.ticker,
            "filter_id": filt.id, "filter_label": filt.label,
            "formula": formula,
            "start": fridays[0].isoformat(), "end": fridays[-1].isoformat(),
            "requested_start": requested_start.isoformat() if requested_start else None,
            "start_clipped": bool(clipped),
            "starting_capital": starting_capital,
            "position_weight_pct": position_weight_pct,
            "stop_multiplier": stop_multiplier,
            "cost_bps": cost_bps, "slippage_bps": slippage_bps,
            "alt_asset_id": alt_opt.id if alt_opt else None,
            "alt_asset_label": alt_opt.label if alt_opt else "",
            "alt_allocation_pct": alt_allocation_pct if alt_opt else 0.0,
            "cash_yield_pct": cash_yield_pct,
        },
        "metrics": m.as_dict(),
        "benchmark": bench.as_dict(),
        "trade_stats": metrics_mod.trade_stats(pf.trades),
        "turnover": metrics_mod.turnover(traded_value, mean_equity, len(equity_rows)),
        "equity_curve": equity_rows,
        "benchmark_curve": [
            {"date": d.date().isoformat(), "equity": round(float(v), 2)}
            for d, v in bench_curve.items()
        ],
        "weeks": weekly_log,
        "trades": [
            {"date": t.date.isoformat(), "symbol": t.symbol, "side": t.side, "qty": t.qty,
             "price": t.price, "value": t.value, "charges": t.charges, "reason": t.reason}
            for t in pf.trades
        ],
        "limitations": ([clipped] if clipped else []) + [
            "Fixed universe (today's index membership applied to all history) — "
            "survivorship bias flatters these results.",
            "The fundamental veto (pledging, interest coverage, pending actions) is not "
            "simulated: those values are not available historically. Per the Rulebook's "
            "data-safeguard, unverifiable checks pass.",
            "Stop fills assume min(stop, next open); real slippage on a gap can be worse.",
            "Prices are yfinance adjusted closes, which differ slightly from NSE prints.",
        ] + ([
            f"RED weeks park {alt_allocation_pct:g}% of equity in {alt_opt.label}. "
            f"That series starts {alt_opt.first_session or 'later than the test'} — earlier "
            "RED weeks simply stay in cash, so the benefit only applies to part of the run.",
        ] if alt_opt else []),
    }
