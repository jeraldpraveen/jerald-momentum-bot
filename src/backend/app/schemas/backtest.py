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

    # A fixed annual yield credited on IDLE cash (not the alt asset). Models a
    # liquid/overnight fund cleanly — no ETF, no dividend-capture problem, and
    # it covers the full window. The default matches the live rule since Spec
    # v2.3 (idle cash is parked in a liquid fund); 0 = truly idle cash.
    cash_yield_pct: float = Field(default=bt_config.CASH_YIELD_PCT, ge=0, le=20)

    # What the run is measured against. None = the regime index (a price index
    # unless it is an ETF proxy). "midsmall400_tri" is the fair comparison but
    # needs a downloaded file — see GET /api/backtest/benchmarks.
    benchmark_id: Optional[str] = None

    # Settle capital-gains tax inside the simulation (today's Indian rates).
    # Off = a pre-tax run, which overstates what a ~6x-turnover strategy keeps.
    apply_tax: bool = bt_config.APPLY_TAX

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
    tax: dict[str, Any] = Field(default_factory=dict)   # {"enabled": False} if pre-tax
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


class BenchmarkOptionOut(BaseModel):
    """A selectable benchmark and whether its data is actually on disk."""

    id: str
    label: str
    source: str                     # "manual" (a downloaded file) | "index"
    total_return: bool = False      # dividends included?
    note: str = ""
    available: bool = False
    first_session: Optional[str] = None
    last_session: Optional[str] = None
    how_to: str = ""                # for a missing manual file: where to get it


class IndexCoverage(BaseModel):
    """How far back a regime index can actually gate a backtest."""

    id: str
    label: str
    ticker: str
    first_session: Optional[str] = None
    last_session: Optional[str] = None
    first_tradable: Optional[str] = None    # after the warm-up
    cached: bool = False


class SweepRunRequest(BaseModel):
    """Configure a walk-forward formula sweep over one universe (or a mix)."""

    universe_id: str
    label: str = ""
    index_id: str = app_config.DEFAULT_INDEX_ID
    filter_id: str = app_config.DEFAULT_FILTER_ID
    train_end: date = date(2016, 12, 31)
    test_start: date = date(2017, 1, 1)

    weight_step: int = Field(default=20, ge=5, le=50)   # grid granularity
    noise_samples: int = Field(default=120, ge=0, le=500)

    cost_bps: float = bt_config.COST_BPS
    slippage_bps: float = bt_config.SLIPPAGE_BPS


class NoiseFloor(BaseModel):
    """How much CAGR spread random formulas produce on the SAME data.

    The reference every optimised result must be read against: an improvement
    smaller than this is indistinguishable from luck.
    """

    n: int = 0
    min_cagr: float = 0.0
    max_cagr: float = 0.0
    mean_cagr: float = 0.0
    std_cagr: float = 0.0
    spread: float = 0.0


class SweepVerdict(BaseModel):
    """The honest read of a sweep — the numbers that expose self-deception.

    Reported together on purpose. `winner_test_cagr` alone is the misleading
    headline; the percentile and beat-rate are what say whether SELECTION did
    anything, or whether the baseline was simply weak for this period.
    """

    baseline_formula: str = ""
    baseline_train_cagr: float = 0.0
    baseline_test_cagr: float = 0.0

    winner_formula: str = ""            # chosen on TRAIN only
    winner_train_cagr: float = 0.0
    winner_test_cagr: float = 0.0
    winner_test_percentile: float = 0.0  # where it landed among all test results
    winner_train_edge: float = 0.0       # vs baseline, in-sample
    winner_test_edge: float = 0.0        # vs baseline, out-of-sample

    pct_beating_baseline_test: float = 0.0   # if most beat it, the baseline is weak
    median_test_cagr: float = 0.0
    best_possible_test_cagr: float = 0.0     # hindsight only — unattainable
    train_test_corr: float = 0.0             # does formula choice transfer at all?
    formulas_tried: int = 0

    @property
    def selection_helped(self) -> bool:
        return self.winner_test_percentile >= 90.0


class SweepRecord(BaseModel):
    sweep_id: str
    label: str = ""
    status: RunStatus = RunStatus.QUEUED
    created_at: datetime
    updated_at: datetime
    request: dict[str, Any] = Field(default_factory=dict)
    progress: RunProgress = Field(default_factory=RunProgress)
    noise: Optional[NoiseFloor] = None
    verdict: Optional[SweepVerdict] = None
    error: str = ""


class SweepResultRow(BaseModel):
    formula: str
    train_cagr: float
    train_sharpe: float
    test_cagr: float
    test_sharpe: float
    test_maxdd: float
    test_turnover: float


class SweepResult(BaseModel):
    sweep_id: str
    config: dict[str, Any] = Field(default_factory=dict)
    noise: NoiseFloor = Field(default_factory=NoiseFloor)
    verdict: SweepVerdict = Field(default_factory=SweepVerdict)
    grid: list[SweepResultRow] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


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
