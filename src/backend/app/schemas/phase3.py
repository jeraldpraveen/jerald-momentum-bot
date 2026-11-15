"""Phase 3 / Step-8 — Monday execution plan schemas (Spec v2.3 §3.1-3.5).

A printable checklist the user executes BY HAND at the open (09:15-09:45).
There is no broker API until promotion (not before 29 January 2027, when the
twelve shadow weeks that restarted on 2026-11-13 complete) — this step
produces instructions and quantities, never orders.

Ordering is mandatory:
  3.1 LIQUIDATIONS FIRST — per ticker: cancel THAT position's GTT, then sell.
      Never bulk-cancel (a bulk Friday cancel leaves you unprotected over the
      weekend gap). Cash updates on fills.
  3.2 Deploy cash into the buy list at ~10% weight; grandfathered legacy
      positions are left untouched (never trimmed to 10%).
  3.3 Stops: stop = FILL price x 0.80, GTT placed the same session.
  3.4 Clear the queues; back to mid-week monitoring.
  3.5 Parking (v2.3): cash the plan does not deploy goes into a liquid or
      overnight fund the same day — RED-week proceeds, and any slot-short
      remainder. On a GREEN week, what the buys need beyond the exit proceeds
      is redeemed from that fund first.
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
    target_value: Optional[float] = None      # weight x capital (the full 10% size)
    last_close: Optional[float] = None
    indicative_qty: Optional[int] = None      # target_value / last close
    indicative_stop: Optional[float] = None   # last close x 0.80 (actual = fill x 0.80)
    # Cash-awareness: buys are funded in rank order up to the cash you actually
    # have. A name that does not fit is deferred, not silently over-deployed.
    funded: bool = True
    defer_reason: str = ""                     # e.g. "insufficient cash"


class Phase3RunRequest(BaseModel):
    """Sizing inputs for the Monday plan.

    `portfolio_capital` sets the 10%-per-name position SIZE (blank => estimated
    from the ledger). `available_cash` limits how many of those positions you
    can actually fund Monday (blank => just the exit proceeds — what your sells
    free up). The two are different numbers, and conflating them is what made
    the old plan tell you to deploy money you did not have.
    """

    portfolio_capital: Optional[float] = None
    available_cash: Optional[float] = None


class Phase3Output(BaseModel):
    as_of: str
    regime: str
    capital_basis: Optional[float] = None
    capital_source: str = ""                  # "entered" | "estimated from ledger"
    stop_pct: float = 20.0

    available_cash: Optional[float] = None     # what can actually be deployed Monday
    cash_source: str = ""                      # "entered" | "from exits"
    deployable: Optional[float] = None         # sum of funded buys
    leftover_cash: Optional[float] = None      # freed but not deployed (a slot short)
    funded_count: int = 0
    deferred_count: int = 0

    exits: list[ExitOrder] = []
    buys: list[BuyOrder] = []

    est_proceeds: Optional[float] = None
    est_deployment: Optional[float] = None

    # Spec v2.3 parking. `park_amount` is what goes INTO the fund this Monday
    # (RED proceeds, or a slot-short remainder); `redeem_amount` is what has to
    # come OUT of it first so the buys can be funded.
    parking_instrument: str = ""
    park_amount: Optional[float] = None
    redeem_amount: Optional[float] = None

    checklist: list[str] = []                 # ordered, human-executable steps
    warnings: list[str] = []
    actions: list[str] = []
