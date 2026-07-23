"""Phase 3 / Step-8 — Monday execution plan schemas (Spec v2.2 §3.1-3.4).

A printable checklist the user executes BY HAND at the open (09:15-09:45).
There is no broker API until the January 2027 promotion — this step produces
instructions and quantities, never orders.

Ordering is mandatory:
  3.1 LIQUIDATIONS FIRST — per ticker: cancel THAT position's GTT, then sell.
      Never bulk-cancel (a bulk Friday cancel leaves you unprotected over the
      weekend gap). Cash updates on fills.
  3.2 Deploy cash into the buy list at ~10% weight; grandfathered legacy
      positions are left untouched (never trimmed to 10%).
  3.3 Stops: stop = FILL price x 0.80, GTT placed the same session.
  3.4 Clear the queues; back to mid-week monitoring.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ExitOrder(BaseModel):
    symbol: str
    qty: Optional[int] = None
    existing_stop: Optional[float] = None     # the GTT to cancel first
    last_close: Optional[float] = None
    est_proceeds: Optional[float] = None      # qty x last close (indicative)
    reason: str = ""                          # rank_exit | regime RED | ...


class BuyOrder(BaseModel):
    symbol: str
    rank: int
    sector: str = ""
    target_weight_pct: float = 10.0
    target_value: Optional[float] = None      # weight x capital
    last_close: Optional[float] = None
    indicative_qty: Optional[int] = None      # target_value / last close
    indicative_stop: Optional[float] = None   # last close x 0.80 (actual = fill x 0.80)


class Phase3RunRequest(BaseModel):
    """Capital basis for sizing. Blank => estimated from the ledger."""

    portfolio_capital: Optional[float] = None


class Phase3Output(BaseModel):
    as_of: str
    regime: str
    capital_basis: Optional[float] = None
    capital_source: str = ""                  # "entered" | "estimated from ledger"
    stop_pct: float = 20.0

    exits: list[ExitOrder] = []
    buys: list[BuyOrder] = []

    est_proceeds: Optional[float] = None
    est_deployment: Optional[float] = None
    checklist: list[str] = []                 # ordered, human-executable steps
    warnings: list[str] = []
    actions: list[str] = []
