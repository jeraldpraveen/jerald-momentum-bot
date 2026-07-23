"""Step-0 routes — the holdings ledger (always editable, not a gated step)."""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas.holdings import (
    Holding,
    HoldingsSaveRequest,
    PortfolioSummary,
    SymbolLookup,
)
from ..services import holdings_service

router = APIRouter(prefix="/api/holdings", tags=["holdings"])


@router.get("", response_model=list[Holding])
def list_holdings() -> list[Holding]:
    return holdings_service.list_holdings()


@router.put("", response_model=list[Holding])
def save_holdings(body: HoldingsSaveRequest) -> list[Holding]:
    """Replace the whole ledger with the supplied rows."""
    return holdings_service.save_holdings(body.holdings)


@router.get("/summary", response_model=PortfolioSummary)
def portfolio_summary(refresh: bool = False) -> PortfolioSummary:
    """Capital deployed, current market value and open risk across held positions.

    `refresh=true` re-pulls live prices instead of serving the cache.
    """
    return holdings_service.summarize(refresh=refresh)


@router.get("/lookup", response_model=SymbolLookup)
def lookup_symbol(symbol: str) -> SymbolLookup:
    """Auto-fill helper: sector, sleeve and last close for a ticker."""
    return holdings_service.lookup_symbol(symbol)
