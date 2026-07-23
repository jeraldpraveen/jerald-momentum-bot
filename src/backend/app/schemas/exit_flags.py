"""Part C — Portfolio Audit (exit flags) schemas.

Cross-references ACTIVE holdings against the Jerald Score ranking from Part B:
a holding ranked worse than the threshold, or no longer in the scanned universe
(rank = infinity), is flagged for exit at Monday open.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class HoldingAudit(BaseModel):
    symbol: str
    sector: str = ""
    rank: Optional[int] = None        # rank in the Part B list; None = not found
    score: Optional[float] = None
    in_universe: bool = True          # False => dropped from the scanned universe
    exit_flagged: bool = False
    reasons: list[str] = []           # e.g. rank>50, dropped_from_universe


class ExitFlagsOutput(BaseModel):
    as_of: str
    rank_threshold: int
    universe_label: str               # which Part B ranking this was audited against
    holdings: list[HoldingAudit]
    exit_count: int
    keep_count: int
    actions: list[str] = []
