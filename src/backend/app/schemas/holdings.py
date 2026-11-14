"""Step-0 — Holdings (the HOLDINGS_LEDGER) schemas.

Holdings are persistent state that spans cycles: they change when a trade fills,
not on the weekly schedule. So this is plain CRUD on the ledger, not a gated
cycle step. Dates are kept as ISO strings for lossless CSV round-tripping.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

HoldingStatus = Literal["ACTIVE", "PENDING_EXIT", "EXITED"]


class Holding(BaseModel):
    symbol: str
    qty: Optional[int] = None
    entry_price: Optional[float] = None
    entry_date: str = ""              # ISO yyyy-mm-dd (blank allowed)
    stop_price: Optional[float] = None
    sleeve: str = ""                  # MIDSMALLCAP400 | MICROCAP250 | ...
    sector: str = ""
    status: HoldingStatus = "ACTIVE"
    exit_reason: str = ""
    marked_on: str = ""
    exit_price: Optional[float] = None    # the price you actually sold at (EXITED)
    entry_charges: Optional[float] = None # brokerage + taxes paid on the BUY
    exit_charges: Optional[float] = None  # brokerage + taxes paid on the SELL


class HoldingsSaveRequest(BaseModel):
    """Replace-all save — matches an editable table in the UI."""

    holdings: list[Holding]


class PositionValue(BaseModel):
    """Per-position valuation. Cost basis is qty x entry_price — both must be set."""

    symbol: str
    status: HoldingStatus
    qty: Optional[int] = None
    entry_price: Optional[float] = None
    cost: Optional[float] = None            # capital deployed into this name
    last_close: Optional[float] = None
    price_date: str = ""          # date of THIS position's close
    market_value: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    weight_pct: Optional[float] = None      # share of current market value
    stop_price: Optional[float] = None
    risk: Optional[float] = None            # loss from last close down to the stop


class PortfolioSummary(BaseModel):
    """Capital deployed and what it is worth now.

    Covers held positions only (ACTIVE + PENDING_EXIT). EXITED rows are excluded:
    the ledger stores no exit price, so realised P&L cannot be computed.
    """

    as_of: str
    price_date: str = ""          # date of the closes used — NOT necessarily today
    held_count: int = 0
    active_count: int = 0
    pending_exit_count: int = 0
    exited_count: int = 0

    capital_deployed: Optional[float] = None    # sum of cost across held positions
    market_value: Optional[float] = None
    unrealised_pnl: Optional[float] = None
    unrealised_pnl_pct: Optional[float] = None
    open_risk: Optional[float] = None           # total loss if every stop fired

    # Realised P&L from EXITED positions that carry an exit price.
    realised_pnl: Optional[float] = None
    realised_pnl_pct: Optional[float] = None
    realised_count: int = 0                      # EXITED rows with an exit price

    positions: list[PositionValue] = []
    incomplete: list[str] = []                  # missing qty or entry price
    unpriced: list[str] = []                    # no price available
    stale_prices: list[str] = []                # priced older than price_date
    warnings: list[str] = []


class SymbolLookup(BaseModel):
    """Best-effort auto-fill when the user types a symbol."""

    symbol: str
    found: bool
    sector: str = ""
    sleeve: str = ""
    last_close: Optional[float] = None
