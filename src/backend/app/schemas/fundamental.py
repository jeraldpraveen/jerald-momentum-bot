"""Part D / Step-4 — Fundamental veto schemas (Spec v2.2, eliminate-only).

Covers 4 of the 7 checks: promoter pledging > 20%, interest coverage < 2
(non-financials), pending structural action, listed < 12 months. Iterates
scanner ranks 1-15 only, excluding names already held.

Data-safeguard rule: a strike requires cited data. Anything missing is marked
"unverified" and PASSES with a flag — the filter never ranks, picks, or adds.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# per-check verdicts
PASS, STRIKE, UNVERIFIED, NA = "pass", "strike", "unverified", "n/a"


class FundamentalAssessment(BaseModel):
    """Human-entered inputs for one candidate (blank => unverified)."""

    pledging_pct: Optional[float] = None       # promoter pledging %, manual
    interest_coverage: Optional[float] = None  # manual override of the auto value
    pending_action: Optional[bool] = None      # True = pending corp/regulatory action
    note: str = ""                             # citation / source


class CheckResult(BaseModel):
    verdict: str                                # pass | strike | unverified | n/a
    value: Optional[float] = None
    detail: str = ""


class FundamentalCandidate(BaseModel):
    symbol: str
    rank: int
    score: Optional[float] = None
    sector: str = ""
    listed_days: int = 0
    is_financial: bool = False
    auto_interest_coverage: Optional[float] = None   # from Yahoo, before override
    surveillance: str = ""                           # NSE ASM/GSM listing, if any
    assessment: FundamentalAssessment = FundamentalAssessment()

    pledging: CheckResult
    interest_cover: CheckResult
    pending_action: CheckResult
    listing_age: CheckResult

    struck: bool = False
    strikes: list[str] = []      # cited failures
    flags: list[str] = []        # unverified items (passed with a flag)


class PartDFundamentalRunRequest(BaseModel):
    """Manual assessments keyed by symbol; preserved across re-runs."""

    assessments: dict[str, FundamentalAssessment] = Field(default_factory=dict)
    refresh_auto: bool = False   # re-pull interest coverage from Yahoo


class FundamentalOutput(BaseModel):
    as_of: str
    rank_window: int
    candidates: list[FundamentalCandidate]
    struck_count: int
    passed_count: int
    # False when NSE's ASM/GSM lists could not be fetched: check 6 then has no
    # exchange data behind it and every candidate is unverified on it.
    surveillance_checked: bool = True
    actions: list[str] = []
