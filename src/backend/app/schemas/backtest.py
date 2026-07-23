"""Backtest API schemas — run configuration, job status, results.

Kept entirely separate from the cycle schemas: a backtest is a research job,
not a step in the Friday ritual, and nothing here can appear in a Cycle.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .. import config as app_config
from ..backtest import config as bt_config
from ..domain import scoring_formula


class RunStatus(str, Enum):
    QUEUED = "queued"
    FETCHING = "fetching"       # pulling prices from yfinance
    RUNNING = "running"         # walking the weeks
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"  # the server stopped mid-run


class BacktestRunRequest(BaseModel):
    """Everything a run needs. Every strategy threshold is a knob, so the later
    ML work can sweep them without code changes."""

    universe_id: str
    label: str = ""                                   # optional human name
    index_id: str = app_config.DEFAULT_INDEX_ID
    filter_id: str = app_config.DEFAULT_FILTER_ID
    formula: str = scoring_formula.DEFAULT_FORMULA
    start: Optional[date] = None
    end: Optional[date] = None

    starting_capital: float = bt_config.STARTING_CAPITAL
    position_weight_pct: float = bt_config.POSITION_WEIGHT_PCT
    stop_multiplier: float = bt_config.STOP_MULTIPLIER
    cost_bps: float = bt_config.COST_BPS
    slippage_bps: float = bt_config.SLIPPAGE_BPS

    rank_exit_threshold: Optional[int] = None
    veto_rank_window: Optional[int] = None

    # Park RED-week capital in an uncorrelated asset instead of idle cash.
    # None = the Rulebook's actual behaviour (cash). This is a research knob:
    # the live ritual is unaffected either way.
    alt_asset_id: Optional[str] = None
    alt_allocation_pct: float = Field(default=100.0, ge=1, le=100)

    fetch_prices: bool = True     # pull anything missing from yfinance first


class RunProgress(BaseModel):
    done: int = 0
    total: int = 0
    pct: float = 0.0
    detail: str = ""


class RunRecord(BaseModel):
    """The light row shown in the run list and polled for progress."""

    run_id: str
    label: str = ""
    status: RunStatus = RunStatus.QUEUED
    created_at: datetime
    updated_at: datetime
    request: dict[str, Any] = Field(default_factory=dict)
    progress: RunProgress = Field(default_factory=RunProgress)
    summary: Optional[dict[str, Any]] = None      # metrics + benchmark, once done
    error: str = ""


class BacktestResult(BaseModel):
    """The full artefact: curves, per-week log and every trade."""

    run_id: str
    config: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    benchmark: dict[str, Any] = Field(default_factory=dict)
    trade_stats: dict[str, Any] = Field(default_factory=dict)
    turnover: float = 0.0
    equity_curve: list[dict[str, Any]] = Field(default_factory=list)
    benchmark_curve: list[dict[str, Any]] = Field(default_factory=list)
    weeks: list[dict[str, Any]] = Field(default_factory=list)
    trades: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class AltAssetOption(BaseModel):
    id: str
    label: str
    ticker: str
    note: str = ""
    first_session: str = ""


class IndexCoverage(BaseModel):
    """How far back a regime index can actually gate a backtest."""

    id: str
    label: str
    ticker: str
    first_session: Optional[str] = None
    last_session: Optional[str] = None
    first_tradable: Optional[str] = None    # after the warm-up
    cached: bool = False


class UniverseSummary(BaseModel):
    universe_id: str
    size: int
    microcap_count: int = 0
    cached_prices: int = 0          # how many members already have history
    first_session: Optional[str] = None
    last_session: Optional[str] = None


class BuildUniverseRequest(BaseModel):
    universe_id: str
    index_ids: list[str] = Field(default_factory=list)
