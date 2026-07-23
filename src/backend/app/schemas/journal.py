"""Part E / Step-7 — Journal finalize schemas (Spec v2.2 §2.11).

The journal row records: date, regime, holding ranks, exits, strike-outs with
cited reasons, unfilled slots, and stops verified (Y/N).

Everything except "stops verified" is compiled automatically from the earlier
steps. A RED week still produces an entry — the record of going to cash matters
as much as the record of a trade.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class JournalHolding(BaseModel):
    symbol: str
    rank: Optional[int] = None       # None = no longer in the scanned universe
    action: str                      # KEEP | EXIT
    reason: str = ""


class JournalBuy(BaseModel):
    symbol: str
    rank: int
    sector: str = ""
    weight_pct: float = 10.0


class JournalStrikeout(BaseModel):
    symbol: str
    rank: int
    step: str                        # fundamental | technical | risk_caps
    reasons: list[str] = []          # cited reasons


class JournalRunRequest(BaseModel):
    stops_verified: Optional[bool] = None   # Y/N — the one manual field
    notes: str = ""


class JournalEntry(BaseModel):
    """One durable weekly row. Appended to journal.jsonl / journal.md on advance."""

    cycle_id: str
    date: str                        # the evaluated Friday (week end)
    logged_at: str

    regime: str                      # GREEN | RED
    regime_detail: str = ""          # e.g. "Supertrend (1, 2.5) on NIFTY 50"

    holdings: list[JournalHolding] = []
    exits: list[str] = []
    buys: list[JournalBuy] = []
    strikeouts: list[JournalStrikeout] = []

    unfilled_slots: int = 0
    final_portfolio_size: int = 0
    stops_verified: Optional[bool] = None
    notes: str = ""


class JournalOutput(BaseModel):
    entry: JournalEntry
    committed: bool = False          # True once written to the journal file
    actions: list[str] = []
