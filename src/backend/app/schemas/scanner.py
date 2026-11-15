"""Part B — Stock Scoring (Jerald Score screener) schemas."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, Field

from .. import config


class ScannerFilters(BaseModel):
    sectors: list[str] = Field(default_factory=list)   # empty = all sectors
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    as_of: Optional[dt.date] = None       # point-in-time date; None = latest


class ScannerCandidate(BaseModel):
    symbol: str
    company: str = ""
    sector: str = ""
    close: Optional[float] = None
    median_tv_cr: Optional[float] = None  # median 20d traded value, Rs crore
    listed_days: int = 0
    score: Optional[float] = None
    rank: Optional[int] = None            # rank among eligible names; None if ineligible
    eligible: bool = False
    flags: list[str] = []                 # e.g. illiquid, listed<12m, price_out_of_range


class PartBRunRequest(BaseModel):
    # One or more universes; their constituents are unioned (deduped by symbol).
    # Defaults to the two Rulebook indices, never to the holdings alone.
    universe_ids: list[str] = Field(
        default_factory=lambda: list(config.DEFAULT_UNIVERSE_IDS))
    # Frozen since Spec v2.3: leave blank (or send the frozen formula). Any
    # other formula is refused with 409 formula_frozen.
    formula: str = ""
    filters: ScannerFilters = ScannerFilters()


class FormulaValidateRequest(BaseModel):
    formula: str


class FormulaValidateResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    terms: list[str] = []                 # term tokens the formula references
    # Whether this is the frozen live formula. A valid formula that is not the
    # frozen one is fine for research but will be refused by the Friday ritual.
    frozen: bool = False


class FrozenFormulaOut(BaseModel):
    """The one formula the live ritual scores with (Spec v2.3)."""

    formula: str
    since: str = "Spec v2.3"
    reason: str = ""


class ScannerOutput(BaseModel):
    universe_ids: list[str]
    universe_label: str                   # combined, e.g. "NIFTY 50 + NIFTY 500"
    as_of: str
    formula: str
    filters: ScannerFilters
    total: int                            # names in the universe
    eligible_count: int
    candidates: list[ScannerCandidate]    # full ranking (eligible first, then flagged)


class UniverseOptionOut(BaseModel):
    id: str
    label: str
    note: str = ""
