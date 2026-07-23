"""The weekly-cycle aggregate: the single source of truth for one Friday ritual.

Persisted as one JSON file per cycle. Each step is a typed StepEnvelope; later
steps (B–E, Phase 3) are placeholder-typed for now and get their real output
models when those parts are built.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .common import StepEnvelope
from .exit_flags import ExitFlagsOutput
from .fundamental import FundamentalOutput
from .journal import JournalOutput
from .phase3 import Phase3Output
from .regime_gate import RegimeGateOutput
from .risk_caps import RiskCapsOutput
from .scanner import ScannerOutput
from .technical import TechnicalOutput


class StepId(str, Enum):
    PART_A_REGIME_GATE = "part_a_regime_gate"
    PART_B_SCANNER = "part_b_scanner"
    PART_C_EXIT_FLAGS = "part_c_exit_flags"
    # Part D — the 7-check eliminate-only veto (v2.2), split into its gates.
    PART_D_FUNDAMENTAL = "part_d_fundamental"
    PART_D_TECHNICAL = "part_d_technical"
    PART_D_RISK_CAPS = "part_d_risk_caps"
    PART_E_JOURNAL = "part_e_journal"
    PHASE3_PLAN = "phase3_plan"


# The ordered GREEN-path progression. RED short-circuits to PHASE3_PLAN.
GREEN_STEP_ORDER = [
    StepId.PART_A_REGIME_GATE,
    StepId.PART_B_SCANNER,
    StepId.PART_C_EXIT_FLAGS,
    StepId.PART_D_FUNDAMENTAL,
    StepId.PART_D_TECHNICAL,
    StepId.PART_D_RISK_CAPS,
    StepId.PART_E_JOURNAL,
    StepId.PHASE3_PLAN,
]

CycleStatus = Literal["open", "closed"]
CycleOutcome = Literal["red_terminated", "green_completed"]


class CycleSteps(BaseModel):
    """Named, per-step envelopes. Only Part A is fully typed this increment."""

    part_a_regime_gate: StepEnvelope[RegimeGateOutput] = Field(default_factory=StepEnvelope)
    part_b_scanner: StepEnvelope[ScannerOutput] = Field(default_factory=StepEnvelope)
    part_c_exit_flags: StepEnvelope[ExitFlagsOutput] = Field(default_factory=StepEnvelope)
    part_d_fundamental: StepEnvelope[FundamentalOutput] = Field(default_factory=StepEnvelope)
    part_d_technical: StepEnvelope[TechnicalOutput] = Field(default_factory=StepEnvelope)
    part_d_risk_caps: StepEnvelope[RiskCapsOutput] = Field(default_factory=StepEnvelope)
    part_e_journal: StepEnvelope[JournalOutput] = Field(default_factory=StepEnvelope)
    phase3_plan: StepEnvelope[Phase3Output] = Field(default_factory=StepEnvelope)


class RegimeDecision(BaseModel):
    """At-a-glance summary of Part A, surfaced at the top of the cycle file."""

    index: str          # index label, e.g. "NIFTY MIDSMALLCAP 400"
    filter: str         # filter label, e.g. "Supertrend (1, 2.5)"
    regime: str         # "GREEN" | "RED"


class IntradayEvent(BaseModel):
    """Phase 1 manual record — a GTT fire / exit the user logs (no polling)."""

    symbol: str
    occurred_on: str
    stop_price: Optional[float] = None
    exit_price: Optional[float] = None
    cash_freed: Optional[float] = None
    note: Optional[str] = None


class Cycle(BaseModel):
    cycle_id: str                       # Friday week-end ISO date, e.g. "2026-07-17"
    created_at: datetime
    updated_at: datetime
    status: CycleStatus = "open"
    current_step: str = StepId.PART_A_REGIME_GATE.value   # StepId value or "done"
    outcome: Optional[CycleOutcome] = None
    regime_decision: Optional[RegimeDecision] = None      # top-level Part A summary
    steps: CycleSteps = Field(default_factory=CycleSteps)
    intraday_events: list[IntradayEvent] = Field(default_factory=list)


class CycleSummary(BaseModel):
    """Row shape for the history list."""

    cycle_id: str
    status: CycleStatus
    outcome: Optional[CycleOutcome] = None
    current_step: str
    created_at: datetime
    updated_at: datetime
