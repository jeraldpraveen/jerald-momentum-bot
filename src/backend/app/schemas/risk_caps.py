"""Part D / Step-6 — Risk caps & slot filling (Spec v2.2 §2.8 + §2.10).

Open slots = target 8-12 names minus the holdings you're keeping. Walk the
Step-5 survivors in strict rank order and add each to NEW_BUY_ORDER unless it
breaches a concentration cap:
  * max 3 names per sector
  * max 4 names from the Microcap 250 sleeve
A breach skips that name and moves to the next rank (still within ranks 1-15).
Unfilled slots stay in CASH until next Friday — they are never force-filled.

Caps count the WHOLE resulting portfolio: kept holdings + new buys.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

MICROCAP_SLEEVE = "MICROCAP250"


class PortfolioSlot(BaseModel):
    """An existing holding being carried into next week."""

    symbol: str
    sector: str = ""
    sleeve: str = ""


class SelectedBuy(BaseModel):
    symbol: str
    rank: int
    sector: str = ""
    sleeve: str = ""
    score: Optional[float] = None
    weight_pct: float = 10.0          # ~10% per name (Rulebook)


class SkippedCandidate(BaseModel):
    symbol: str
    rank: int
    sector: str = ""
    sleeve: str = ""
    reason: str                       # sector_cap | microcap_cap | slots_full


class RiskCapsOutput(BaseModel):
    as_of: str
    target_min: int
    target_max: int
    max_per_sector: int
    max_microcap: int

    kept: list[PortfolioSlot]         # survivors already in the portfolio
    open_slots: int
    buys: list[SelectedBuy]           # the NEW_BUY_ORDER queue
    skipped: list[SkippedCandidate]
    unfilled_slots: int               # stays in cash

    sector_counts: dict[str, int]     # resulting portfolio
    microcap_count: int
    final_portfolio_size: int
    # False when the Microcap 250 list could not be loaded, so the max-microcap
    # cap had nothing to count against and was NOT applied.
    microcap_cap_enforced: bool = True
    actions: list[str] = []
