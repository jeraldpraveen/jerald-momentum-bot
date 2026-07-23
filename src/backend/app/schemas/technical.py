"""Part D / Step-5 — Technical veto schemas (Spec v2.2 checks 3/4/5).

  3. Price below its 50-DMA          -> not in an uptrend
  4. Price > 30% above its 20-DMA    -> too extended, poor entry
  5. RSI(14) > 82                    -> overbought

All three are computed from cached price history, so every verdict is cited —
there is no manual input and nothing is ever "unverified" unless a stock lacks
enough history. Only candidates that survived Step-4 are evaluated.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .fundamental import CheckResult


class TechnicalCandidate(BaseModel):
    symbol: str
    rank: int
    sector: str = ""
    close: Optional[float] = None
    dma_50: Optional[float] = None
    dma_20: Optional[float] = None
    extension_pct: Optional[float] = None    # % above the 20-DMA
    rsi_14: Optional[float] = None

    trend: CheckResult                        # check 3
    extension: CheckResult                    # check 4
    overbought: CheckResult                   # check 5

    struck: bool = False
    strikes: list[str] = []
    flags: list[str] = []


class TechnicalOutput(BaseModel):
    as_of: str
    candidates: list[TechnicalCandidate]
    struck_count: int
    passed_count: int
    survivors: list[str] = []                 # symbols carried into the risk caps
    actions: list[str] = []
