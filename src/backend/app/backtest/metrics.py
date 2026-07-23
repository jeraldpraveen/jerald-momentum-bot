"""Performance metrics from a weekly equity curve.

Weekly periodicity throughout (52 periods/year) because the strategy rebalances
weekly — annualising daily stats off weekly data would overstate Sharpe.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

WEEKS_PER_YEAR = 52.0


@dataclass
class Metrics:
    start_equity: float = 0.0
    end_equity: float = 0.0
    total_return_pct: float = 0.0
    cagr_pct: float = 0.0
    volatility_pct: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_weeks: int = 0
    weeks: int = 0
    exposure_pct: float = 0.0            # share of weeks holding anything
    win_rate_pct: float = 0.0            # share of ALL weeks with a positive return
    win_rate_invested_pct: float = 0.0   # ...counting only weeks actually invested

    def as_dict(self) -> dict:
        return asdict(self)


def _drawdown(equity: pd.Series) -> tuple[float, int]:
    peak = equity.cummax()
    dd = equity / peak - 1.0
    worst = float(dd.min()) * 100 if len(dd) else 0.0

    longest = current = 0
    for below in (equity < peak).tolist():
        current = current + 1 if below else 0
        longest = max(longest, current)
    return round(worst, 2), longest


def compute(equity: pd.Series, exposure: list[bool] | None = None,
            risk_free_pct: float = 0.0) -> Metrics:
    """Metrics for a weekly equity series indexed by week-end date."""
    equity = equity.dropna()
    if len(equity) < 2:
        start = float(equity.iloc[0]) if len(equity) else 0.0
        return Metrics(start_equity=start, end_equity=start, weeks=len(equity))

    rets = equity.pct_change().dropna()
    start, end = float(equity.iloc[0]), float(equity.iloc[-1])
    years = len(rets) / WEEKS_PER_YEAR

    total_return = (end / start - 1) * 100 if start else 0.0
    cagr = ((end / start) ** (1 / years) - 1) * 100 if start > 0 and years > 0 else 0.0

    vol_weekly = float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
    vol_annual = vol_weekly * math.sqrt(WEEKS_PER_YEAR) * 100

    rf_weekly = risk_free_pct / 100 / WEEKS_PER_YEAR
    excess = rets - rf_weekly
    sharpe = (float(excess.mean()) / vol_weekly * math.sqrt(WEEKS_PER_YEAR)) if vol_weekly else 0.0

    downside = excess[excess < 0]
    dstd = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = (float(excess.mean()) / dstd * math.sqrt(WEEKS_PER_YEAR)) if dstd else 0.0

    max_dd, dd_weeks = _drawdown(equity)

    # A week in cash returns exactly 0, which is not "> 0" — so a strategy that
    # sits out half the time scores a dismal win rate against an always-invested
    # index. That comparison is meaningless, so report the invested-weeks rate
    # too. Exposure is offset by one: rets[i] is the move INTO week i.
    invested_rate = 0.0
    if exposure and len(exposure) >= len(rets) + 1:
        flags = pd.Series(exposure[1:len(rets) + 1], index=rets.index)
        live = rets[flags.values]
        invested_rate = round(100 * float((live > 0).mean()), 2) if len(live) else 0.0

    return Metrics(
        start_equity=round(start, 2), end_equity=round(end, 2),
        total_return_pct=round(total_return, 2), cagr_pct=round(cagr, 2),
        volatility_pct=round(vol_annual, 2),
        sharpe=round(sharpe, 3), sortino=round(sortino, 3),
        max_drawdown_pct=max_dd, max_drawdown_weeks=dd_weeks,
        weeks=len(equity),
        exposure_pct=round(100 * sum(exposure) / len(exposure), 2) if exposure else 0.0,
        win_rate_pct=round(100 * float((rets > 0).mean()), 2),
        win_rate_invested_pct=invested_rate,
    )


def buy_and_hold(prices: pd.Series, starting_capital: float) -> pd.Series:
    """Benchmark curve: the same capital left in the index from day one."""
    prices = prices.dropna()
    if prices.empty:
        return pd.Series(dtype=float)
    return starting_capital * prices / float(prices.iloc[0])


def turnover(trades_value: float, mean_equity: float, weeks: int) -> float:
    """Annualised turnover as a multiple of portfolio value."""
    if mean_equity <= 0 or weeks <= 0:
        return 0.0
    years = weeks / WEEKS_PER_YEAR
    return round(trades_value / mean_equity / years, 2) if years else 0.0


def trade_stats(trades) -> dict:
    """Realised win rate and average P&L per round trip (FIFO by symbol)."""
    opens: dict[str, list] = {}
    wins = losses = 0
    pnls: list[float] = []

    for t in trades:
        if t.side == "buy":
            opens.setdefault(t.symbol, []).append(t)
        else:
            lots = opens.get(t.symbol) or []
            cost = sum(l.value + l.charges for l in lots)
            if not lots:
                continue
            pnl = (t.value - t.charges) - cost
            pnls.append(pnl)
            wins += pnl > 0
            losses += pnl <= 0
            opens[t.symbol] = []

    closed = wins + losses
    return {
        "closed_trades": closed,
        "win_rate_pct": round(100 * wins / closed, 2) if closed else 0.0,
        "avg_pnl": round(float(np.mean(pnls)), 2) if pnls else 0.0,
        "best_pnl": round(max(pnls), 2) if pnls else 0.0,
        "worst_pnl": round(min(pnls), 2) if pnls else 0.0,
    }
