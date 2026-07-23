"""Part B — Stock Scoring (Jerald Score screener) schemas."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from pydantic import BaseModel, Field


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
    universe_ids: list[str] = Field(default_factory=lambda: ["holdings"])
    formula: str = ""                     # empty => the default Jerald formula
    filters: ScannerFilters = ScannerFilters()


class FormulaValidateRequest(BaseModel):
    formula: str


class FormulaValidateResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    terms: list[str] = []                 # term tokens the formula references


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
