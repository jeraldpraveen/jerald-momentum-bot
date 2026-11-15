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
import math

import pandas as pd

from .. import config as app_config
from ..domain import scoring_formula
from ..domain import strategy
from ..schemas.scanner import ScannerFilters
from . import benchmarks
from . import config as bt_config
from . import metrics as metrics_mod
from . import price_store, universe
from . import tax as tax_mod
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
    if rows.empty:
        return None
    price = float(rows["open"].iloc[0])
    return price if math.isfinite(price) and price > 0 else None


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
    cash_yield_pct: float = bt_config.CASH_YIELD_PCT,
    benchmark_id: str | None = None,
    apply_tax: bool = bt_config.APPLY_TAX,
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

    # Resolve the benchmark BEFORE walking the weeks: a missing total-return
    # file should fail in a second, not after a two-hour run.
    try:
        bench_opt = benchmarks.get_option(benchmark_id, index_opt)
        bench_daily = benchmarks.series(bench_opt)
    except benchmarks.BenchmarkError as exc:
        raise BacktestError(str(exc)) from exc

    index_weekly = resample_weekly(index_daily)
    index_days = [d.date() for d in index_daily.index]

    # Warm-up: the score needs a year of candles before the first decision.
    warmup_end = index_days[0] + dt.timedelta(days=bt_config.MIN_WARMUP_DAYS)
    fridays = [d.date() for d in index_weekly.index[1:]]
    fridays = [f for f in fridays if f >= warmup_end and f <= index_days[-1]]
    if start:
        fridays = [f for f in fridays if f >= start]
    if end:
        fridays = [f for f in fridays if f <= end]
    if len(fridays) < 2:
        raise BacktestError(
            "No tradable weeks in that range — at least two weekly marks are needed.")

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

    bench_weekly = bench_daily.resample("W-FRI").last().reindex(pd.to_datetime(fridays))
    if bench_weekly.isna().any():
        raise BacktestError(
            f"The benchmark {bench_opt.label} has missing weekly data between "
            f"{fridays[0].isoformat()} and {fridays[-1].isoformat()}. "
            "Pick a fully covered date range or another benchmark.")

    tickers = [m.ticker for m in members]
    sector_of = {m.symbol: m.sector for m in members}
    sleeve_of = {m.symbol: m.sleeve for m in members}
    micro = universe.microcap_symbols(members)

    weekly_cash_yield = (1 + cash_yield_pct / 100) ** (1 / metrics_mod.WEEKS_PER_YEAR) - 1
    pf = Portfolio(cash=starting_capital, costs=costs,
                   tax=tax_mod.TaxLedger() if apply_tax else None)
    mark_prices: dict[str, float] = {}
    mark_date = fridays[-1]
    equity_rows: list[dict] = [{
        "date": fridays[0].isoformat(), "equity": round(starting_capital, 2),
        "cash": round(starting_capital, 2), "positions": 0, "regime": "CASH",
        "alt_value": 0.0,
    }]
    weekly_log: list[dict] = []
    exposure: list[bool] = [False]
    traded_value = 0.0

    for i, friday in enumerate(fridays[:-1]):
        if progress and i % 10 == 0:
            progress(i, len(fridays), friday)

        # --- Friday close: decide -----------------------------------------
        weekly_slice = index_weekly[index_weekly.index.date <= friday]
        regime, line_label, line_value = strategy.decide_regime(weekly_slice, filt)

        prices = price_store.get_many(tickers, as_of=friday)
        closes = {t[:-3]: float(df["close"].iloc[-1]) for t, df in prices.items()}

        exits: list[str] = []
        buys: list[strategy.RankedName] = []
        deferred: list[str] = []
        unfilled_buys: list[str] = []
        unfilled_exits: list[str] = []
        blocked_buys: list[str] = []
        candidates = []

        if regime == strategy.RED:
            exits = pf.symbols()
        else:
            candidates = strategy.build_candidates(
                [(m.ticker, m.sector, m.symbol) for m in members], prices, compiled,
                ScannerFilters(as_of=friday),      # so stale prices are caught here too
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
                px = opens.get(sym)
                if px is not None:
                    t = pf.sell(sym, px, monday,
                                reason="regime_red" if regime == strategy.RED else "rank_exit")
                    if t:
                        traded_value += t.value
                else:
                    unfilled_exits.append(sym)

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
                    if unfilled_exits:
                        blocked_buys.append(b.symbol)
                        continue
                    px = opens.get(b.symbol)
                    if px is None:
                        unfilled_buys.append(b.symbol)
                        continue
                    # Not enough cash for a full position: defer it, exactly as
                    # the live Monday plan does — never open an undersized one.
                    if pf.cash < budget_each * bt_config.MIN_FILL_FRACTION:
                        deferred.append(b.symbol)
                        continue
                    t = pf.buy(b.symbol, px, min(budget_each, pf.cash), monday,
                               stop_multiplier=stop_multiplier,
                               sector=sector_of.get(b.symbol, ""),
                               sleeve=sleeve_of.get(b.symbol, ""),
                               reason=f"rank_{b.rank}")
                    if t:
                        traded_value += t.value

        # --- through the week: stops --------------------------------------
        next_friday = fridays[i + 1]
        stop_hits = _apply_stops(pf, tickers, monday, next_friday, traded_value)
        traded_value = stop_hits["traded_value"]

        # --- mark to market at the next Friday close ----------------------
        mark_date = next_friday

        # --- interest on idle cash (the liquid-fund parking of Spec v2.3) --
        if weekly_cash_yield and pf.cash > 0:
            interest = pf.cash * weekly_cash_yield
            pf.cash += interest
            if pf.tax is not None:
                pf.cash -= pf.tax.cash_income(mark_date, interest)

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
            "deferred": deferred,                 # selected, but not enough cash
            "unfilled_buys": unfilled_buys, "unfilled_exits": unfilled_exits,
            "blocked_buys": blocked_buys,
            "stops_hit": stop_hits["symbols"],
            "held": pf.symbols(), "equity": round(eq, 2),
            "eligible": sum(1 for c in candidates if c.eligible),
        })

    return _assemble(
        pf, equity_rows, weekly_log, exposure, traded_value,
        bench_opt=bench_opt, bench_window=bench_weekly,
        mark_prices=mark_prices, mark_date=mark_date,
        fridays=fridays, starting_capital=starting_capital,
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

    Fill = min(stop, that day's open), less configured slippage.
    """
    hits: list[str] = []
    if start is None or end is None:
        return {"symbols": hits, "traded_value": traded_value}

    for sym in pf.symbols():
        h = pf.holdings[sym]
        df = price_store.get(f"{sym}.NS", as_of=end)
        if df is None:
            continue
        window = df[(df.index.date >= start) & (df.index.date <= end)]
        for ts, row in window.iterrows():
            if float(row["low"]) <= h.stop_price:
                day_open = float(row["open"])
                if not math.isfinite(day_open) or day_open <= 0:
                    raise BacktestError(
                        f"Cannot price stop for {sym} on {ts.date()}: invalid open.")
                fill = min(h.stop_price, day_open)
                fill = pf.costs.fill_price(fill, "sell")
                t = pf.sell(sym, fill, ts.date(), reason="stop_hit", fill_price=fill)
                if t:
                    traded_value += t.value
                    hits.append(sym)
                break
    return {"symbols": hits, "traded_value": traded_value}


def _tax_block(pf: Portfolio, curve: pd.Series, bench_curve: pd.Series,
               mark_prices: dict[str, float], mark_date: dt.date) -> dict:
    """After-tax comparison on equal terms: both sides sold on the last day.

    The strategy's curve is already net of tax on everything it SOLD, but its
    open positions still carry untaxed gains; the benchmark has paid nothing at
    all. Charging each its exit tax is the only like-for-like end figure.
    """
    if pf.tax is None:
        return {"enabled": False}

    def side(start: float, end: float, exit_tax: float, periods: int) -> dict:
        after = end - exit_tax
        return {"exit_tax": round(exit_tax, 2),
                "end_equity_after_exit": round(after, 2),
                "cagr_after_exit_pct": round(metrics_mod.cagr_pct(start, after, periods), 2)}

    rules = pf.tax.rules
    s_start, s_end = float(curve.iloc[0]), float(curve.iloc[-1])
    b_start, b_end = float(bench_curve.iloc[0]), float(bench_curve.iloc[-1])
    held_days = (bench_curve.index[-1] - bench_curve.index[0]).days
    return {
        "enabled": True,
        **pf.tax.summary(),
        "strategy": side(s_start, s_end, pf.liquidation_tax(mark_prices, mark_date),
                         len(curve) - 1),
        "benchmark": side(b_start, b_end,
                          tax_mod.exit_tax(b_end - b_start, held_days, rules),
                          len(bench_curve) - 1),
    }


def _assemble(pf, equity_rows, weekly_log, exposure, traded_value, *, bench_opt,
              bench_window, mark_prices, mark_date,
              fridays, starting_capital, universe_id, index_opt, filt, formula,
              position_weight_pct, stop_multiplier, cost_bps, slippage_bps, members,
              alt_opt=None, alt_allocation_pct=100.0, clipped=None,
              requested_start=None, cash_yield_pct=0.0) -> dict:
    curve = pd.Series(
        [r["equity"] for r in equity_rows],
        index=pd.to_datetime([r["date"] for r in equity_rows]),
    )
    m = metrics_mod.compute(curve, exposure)

    bench_weekly = bench_window
    bench_curve = metrics_mod.buy_and_hold(bench_weekly, starting_capital)
    bench = metrics_mod.compute(bench_curve)
    bench_start = bench_weekly.index[0].date()
    tax_block = _tax_block(pf, curve, bench_curve, mark_prices, mark_date)

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
            "benchmark_id": bench_opt.id, "benchmark_label": bench_opt.label,
            "benchmark_total_return": bench_opt.total_return,
            "benchmark_start": bench_start.isoformat(),
            "benchmark_end": bench_weekly.index[-1].date().isoformat(),
            "apply_tax": pf.tax is not None,
        },
        "metrics": m.as_dict(),
        "benchmark": bench.as_dict(),
        "tax": tax_block,
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
             "price": t.price, "value": t.value, "charges": t.charges, "reason": t.reason,
             "pnl": t.pnl, "tax": t.tax}
            for t in pf.trades
        ],
        "limitations": ([clipped] if clipped else []) + [
            "Fixed universe (today's index membership applied to all history) — "
            "survivorship bias flatters these results.",
            "The fundamental veto (pledging, interest coverage, pending actions) is not "
            "simulated: those values are not available historically. Per the Rulebook's "
            "data-safeguard, unverifiable checks pass.",
            "Stop fills use min(stop, day's open) less configured slippage, including the "
            "execution day. Daily bars cannot establish when a stop was placed or whether "
            "an order filled; gaps, halts and market impact can make actual fills worse.",
            "Missing execution opens leave orders unfilled; missing or stale prices can "
            "also make marked portfolio values unreliable.",
            "When an exit has no execution open, that week's planned buys are blocked "
            "because the portfolio still contains the unsold position.",
            "Prices are yfinance adjusted closes, which differ slightly from NSE prints.",
            "Vendor-tick repairs may use surrounding observations, including later prices.",
            "The benchmark is an index or proxy, not a model of actual fund expenses "
            "and tracking differences.",
        ] + ([
            "Tax is modelled at TODAY'S rates for the whole history (short-term 20%, "
            "long-term 12.5% above Rs 1.25 lakh a year, parked-cash income at 30%) and paid "
            "as gains are realised. Surcharge and cess are not included, so the real bill "
            "is slightly higher.",
        ] if pf.tax is not None else [
            "Tax is NOT modelled in this run. The strategy turns over about six times a "
            "year, so nearly every gain is short-term — pre-tax returns overstate what "
            "you would keep.",
        ]) + ([] if bench_opt.total_return else [
            f"The benchmark {bench_opt.label} is a PRICE index: it leaves out dividends "
            "(roughly 1% a year), so the strategy's lead over it is overstated by about "
            "that much. Use a total-return benchmark for a fair comparison.",
        ]) + ([
            f"Idle cash is credited a fixed {cash_yield_pct:g}% a year, standing in for a "
            "liquid or overnight fund. Real yields moved between roughly 3% and 9% over "
            "this period.",
        ] if cash_yield_pct else []) + ([
            f"RED weeks park {alt_allocation_pct:g}% of equity in {alt_opt.label}. "
            f"That series starts {alt_opt.first_session or 'later than the test'} — earlier "
            "RED weeks simply stay in cash, so the benefit only applies to part of the run.",
        ] if alt_opt else []),
    }
