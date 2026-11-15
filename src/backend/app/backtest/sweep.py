"""Fast panel-driven simulation, for sweeping many formulas.

The full engine takes ~28 minutes per 20-year run because it recomputes every
score term every week. Against a prebuilt panel the same walk takes about a
second, which is what makes a 200-variant sweep possible.

**This is a SCREEN, not the judge.** It reuses the real rules (`apply_caps`,
`audit_holdings`, and the same technical thresholds) but simplifies execution:
stops are checked against the week's low rather than day by day, and fills use
the next open. Those choices can move CAGR by a few tenths of a point, so the
protocol is: screen fast, then confirm the shortlist with the full engine.
`test_sweep_tracks_the_full_engine` pins how closely the two agree.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from .. import config as app_config
from ..domain import scoring_formula, strategy
from . import metrics as metrics_mod
from . import panel as panel_mod
from . import config as bt_config
from .costs import CostModel


@dataclass
class SweepResult:
    formula: str
    cagr_pct: float
    sharpe: float
    max_drawdown_pct: float
    volatility_pct: float
    end_equity: float
    weeks: int
    exposure_pct: float
    turnover: float
    trades: int

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def simulate(p: panel_mod.Panel,
             scores: np.ndarray,
             regime: np.ndarray,
             *,
             starting_capital: float = 1_000_000.0,
             position_weight_pct: float = 10.0,
             stop_multiplier: float = 0.80,
             cost_bps: float = 30.0,
             slippage_bps: float = 25.0,
             rank_exit_threshold: int | None = None,
             veto_rank_window: int | None = None,
             max_extension_pct: float | None = None,
             max_rsi: float | None = None) -> SweepResult:
    """Walk the panel week by week with a given score matrix and regime vector."""
    rank_exit = rank_exit_threshold or app_config.RANK_EXIT_THRESHOLD
    window = veto_rank_window or app_config.VETO_RANK_WINDOW
    ext_cap = app_config.MAX_EXTENSION_PCT if max_extension_pct is None else max_extension_pct
    rsi_cap = app_config.MAX_RSI if max_rsi is None else max_rsi

    costs = CostModel(cost_bps=cost_bps, slippage_bps=slippage_bps)
    eligible = p.eligible()
    stale = p.stale()
    rk = panel_mod.ranks(scores, eligible)
    micro = {s for s, sl in zip(p.symbols, p.sleeve) if sl == "MICROCAP250"}
    sector_of = dict(zip(p.symbols, p.sector))

    cash = starting_capital
    qty: dict[str, float] = {}
    stop: dict[str, float] = {}
    equity_curve, exposure = [], []
    traded_value = 0.0
    trades = 0

    for i, _week in enumerate(p.weeks):
        close_i = p.close[i]
        px = {s: close_i[j] for j, s in enumerate(p.symbols) if np.isfinite(close_i[j])}

        # --- decide (Friday close) -----------------------------------------
        if regime[i] == 0:                                   # RED
            exits = list(qty)
            buys: list[strategy.RankedName] = []
        else:
            held = [strategy.Position(s, sector_of.get(s, "")) for s in qty]
            ranked_now = {s: int(rk[i, j]) for j, s in enumerate(p.symbols)}
            # A holding with no current price is kept, not treated as dropped —
            # the audit rule: a data gap is not an exit signal.
            gap = {s for j, s in enumerate(p.symbols) if stale[i, j]}
            exits = [s for s in qty if s not in gap
                     and (ranked_now.get(s, 0) == 0 or ranked_now[s] > rank_exit)]
            keep = [s for s in qty if s not in exits]

            shortlist = []
            for j, s in enumerate(p.symbols):
                r = int(rk[i, j])
                if r == 0 or r > window or s in keep or s in exits:
                    continue
                # The same three technical checks, from precomputed columns.
                c, d50, d20, rsi = close_i[j], p.dma_50[i, j], p.dma_20[i, j], p.rsi_14[i, j]
                if np.isfinite(d50) and c < d50:
                    continue
                if np.isfinite(d20) and (c / d20 - 1) * 100 > ext_cap:
                    continue
                if np.isfinite(rsi) and rsi > rsi_cap:
                    continue
                shortlist.append(strategy.RankedName(s, r, sector_of.get(s, "")))

            caps = strategy.apply_caps(
                shortlist,
                [strategy.Position(s, sector_of.get(s, "")) for s in keep],
                micro)
            buys = caps.buys

        # --- execute (next open) -------------------------------------------
        open_i = p.next_open[i]
        for s in exits:
            j = p.symbols.index(s)
            ref = open_i[j] if np.isfinite(open_i[j]) else px.get(s)
            if ref:
                fill = costs.fill_price(ref, "sell")
                value = qty[s] * fill
                cash += value - costs.charges(value)
                traded_value += value
                trades += 1
                qty.pop(s), stop.pop(s, None)

        if regime[i] != 0 and buys:
            budget = (cash + sum(qty[s] * px.get(s, 0.0) for s in qty)) \
                * position_weight_pct / 100
            for b in buys:
                j = p.symbols.index(b.symbol)
                ref = open_i[j] if np.isfinite(open_i[j]) else px.get(b.symbol)
                if not ref:
                    continue
                # Defer rather than under-size — the same rule as the engine and
                # the live Monday plan.
                if cash < budget * bt_config.MIN_FILL_FRACTION:
                    continue
                fill = costs.fill_price(ref, "buy")
                # Leave room for the charges, so a buy that takes the last of the
                # cash is a share or two short rather than dropped altogether
                # (the engine does the same).
                affordable = cash / (1 + costs.cost_bps / 10_000.0)
                n = int(min(budget, affordable) // fill)
                if n < 1:
                    continue
                value = n * fill
                charge = costs.charges(value)
                cash -= value + charge
                qty[b.symbol] = qty.get(b.symbol, 0) + n
                stop[b.symbol] = fill * stop_multiplier
                traded_value += value
                trades += 1

        # --- stops during the week ------------------------------------------
        for s in list(qty):
            j = p.symbols.index(s)
            low = p.week_low[i, j]
            if np.isfinite(low) and low <= stop[s]:
                fill = min(stop[s], low)      # a gap fills below the stop
                value = qty[s] * fill
                cash += value - costs.charges(value)
                traded_value += value
                trades += 1
                qty.pop(s), stop.pop(s, None)

        # --- mark to market --------------------------------------------------
        nxt = min(i + 1, len(p.weeks) - 1)
        mark = p.close[nxt]
        held_value = sum(
            q * (mark[p.symbols.index(s)] if np.isfinite(mark[p.symbols.index(s)])
                 else px.get(s, 0.0))
            for s, q in qty.items())
        equity_curve.append(cash + held_value)
        exposure.append(bool(qty))

    import pandas as pd
    curve = pd.Series(equity_curve, index=pd.to_datetime(p.weeks))
    m = metrics_mod.compute(curve, exposure)
    mean_eq = float(curve.mean()) if len(curve) else 0.0

    return SweepResult(
        formula="", cagr_pct=m.cagr_pct, sharpe=m.sharpe,
        max_drawdown_pct=m.max_drawdown_pct, volatility_pct=m.volatility_pct,
        end_equity=m.end_equity, weeks=m.weeks, exposure_pct=m.exposure_pct,
        turnover=metrics_mod.turnover(traded_value, mean_eq, len(curve)),
        trades=trades,
    )


def regime_vector(p: panel_mod.Panel, filter_id: str | None = None) -> np.ndarray:
    """1 = GREEN, 0 = RED for each panel week, from the shared regime rule."""
    from .engine import resample_weekly
    from . import price_store

    filt = app_config.get_filter_option(filter_id)
    daily = price_store.load(p.meta["index_ticker"])
    weekly = resample_weekly(daily)

    out = np.ones(len(p.weeks), dtype=int)
    for i, wk in enumerate(p.weeks):
        sl = weekly[weekly.index.date <= wk]
        if len(sl) < 2:
            continue
        out[i] = 1 if strategy.decide_regime(sl, filt)[0] == strategy.GREEN else 0
    return out


def evaluate(p: panel_mod.Panel, formula: str, regime: np.ndarray, **kw) -> SweepResult:
    """Score one formula across the panel and simulate it."""
    compiled = scoring_formula.compile_formula(formula)
    res = simulate(p, panel_mod.score_panel(p, compiled), regime, **kw)
    res.formula = formula
    return res


def slice_panel(p: panel_mod.Panel, start: dt.date | None,
                end: dt.date | None) -> tuple[panel_mod.Panel, np.ndarray]:
    """A date-restricted view of the panel, plus the index mask used.

    Walk-forward evaluation needs train and test slices that cannot overlap.
    """
    mask = np.array([(start is None or w >= start) and (end is None or w <= end)
                     for w in p.weeks])
    idx = np.where(mask)[0]
    sub = panel_mod.Panel(
        weeks=[p.weeks[i] for i in idx], symbols=p.symbols,
        terms={t: a[idx] for t, a in p.terms.items()},
        close=p.close[idx], median_tv_cr=p.median_tv_cr[idx],
        listed_days=p.listed_days[idx], dma_50=p.dma_50[idx], dma_20=p.dma_20[idx],
        rsi_14=p.rsi_14[idx], week_low=p.week_low[idx], next_open=p.next_open[idx],
        sector=p.sector, sleeve=p.sleeve, meta=dict(p.meta),
        price_age_days=None if p.price_age_days is None else p.price_age_days[idx],
    )
    return sub, mask
