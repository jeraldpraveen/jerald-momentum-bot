"""Simulated portfolio — positions, cash, stops, and the trade log.

Deliberately dumb about strategy: it executes what it is told and keeps the
books. Every decision about WHAT to trade comes from the shared strategy core.
"""

from __future__ import annotations

import copy
import datetime as dt
from dataclasses import dataclass, field

from .costs import CostModel
from .tax import TaxLedger


@dataclass
class Holding:
    symbol: str
    qty: int
    entry_price: float          # actual fill, after slippage
    entry_date: dt.date
    stop_price: float
    sector: str = ""
    sleeve: str = "MIDSMALLCAP400"
    cost: float = 0.0           # total paid incl. charges — the tax cost basis

    def value(self, price: float) -> float:
        return self.qty * price

    def basis(self) -> float:
        """What the position cost. Falls back to qty x entry for a holding
        created without a recorded cost (it then ignores the buy charges)."""
        return self.cost if self.cost > 0 else self.qty * self.entry_price


@dataclass
class Trade:
    date: dt.date
    symbol: str
    side: str                   # "buy" | "sell"
    qty: int
    price: float                # fill price after slippage
    value: float
    charges: float
    reason: str = ""
    pnl: float | None = None    # realised gain net of both legs' charges (sells)
    tax: float = 0.0            # tax paid (+) or refunded (-) because of this sale


@dataclass
class Portfolio:
    cash: float
    costs: CostModel = field(default_factory=CostModel)
    holdings: dict[str, Holding] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    # None = a pre-tax simulation. With a ledger, every sale settles the change
    # in the year-to-date liability straight out of cash.
    tax: TaxLedger | None = None
    # The RED-regime parking asset (gold, a liquid fund, …). Deliberately NOT in
    # `holdings`: it must never reach the strategy core, or it would be ranked,
    # rank-exited and stopped out like an equity position.
    alt: Holding | None = None

    # --- reads --------------------------------------------------------------
    def symbols(self) -> list[str]:
        return sorted(self.holdings)

    def market_value(self, prices: dict[str, float]) -> float:
        """Value of held names (including the alt asset) at the given prices.

        A symbol with no price contributes its last known cost basis rather than
        zero — a missing quote means "unknown", not "worthless".
        """
        total = 0.0
        for sym, h in self.holdings.items():
            px = prices.get(sym)
            total += h.value(px) if px is not None else h.qty * h.entry_price
        if self.alt is not None:
            px = prices.get(self.alt.symbol)
            total += self.alt.value(px) if px is not None else self.alt.qty * self.alt.entry_price
        return total

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def liquidation_tax(self, prices: dict[str, float], on: dt.date) -> float:
        """Tax that selling everything on `on` would trigger. Books untouched.

        Used at the end of a run: open positions carry untaxed gains, and the
        buy-and-hold benchmark is charged its exit tax, so the strategy must be
        too or the comparison quietly favours whichever side defers more.
        Unpriced names are skipped — an unknown price is not a known gain.
        """
        if self.tax is None:
            return 0.0
        ledger = copy.deepcopy(self.tax)
        total = 0.0
        for h in self.holdings.values():
            px = prices.get(h.symbol)
            if px is not None:
                total += ledger.realise(on, h.value(px) - h.basis(), (on - h.entry_date).days)
        if self.alt is not None:
            px = prices.get(self.alt.symbol)
            if px is not None:
                total += ledger.cash_income(on, self.alt.value(px) - self.alt.basis())
        return total

    # --- writes -------------------------------------------------------------
    def buy(self, symbol: str, reference_price: float, budget: float, on: dt.date,
            *, stop_multiplier: float, sector: str = "", sleeve: str = "",
            reason: str = "") -> Trade | None:
        """Buy as many whole shares as `budget` affords. None if it affords zero."""
        price = self.costs.fill_price(reference_price, "buy")
        if price <= 0:
            return None
        qty = int(budget // price)
        if qty < 1:
            return None

        value = qty * price
        charges = self.costs.charges(value)
        if value + charges > self.cash:                 # never go overdrawn
            qty = int((self.cash / (1 + self.costs.cost_bps / 10_000.0)) // price)
            if qty < 1:
                return None
            value = qty * price
            charges = self.costs.charges(value)

        self.cash -= value + charges
        existing = self.holdings.get(symbol)
        if existing:                                    # average up, keep the older stop date
            total_qty = existing.qty + qty
            existing.cost = existing.basis() + value + charges
            existing.entry_price = (existing.value(existing.entry_price) + value) / total_qty
            existing.qty = total_qty
            existing.stop_price = existing.entry_price * stop_multiplier
        else:
            self.holdings[symbol] = Holding(
                symbol=symbol, qty=qty, entry_price=price, entry_date=on,
                stop_price=price * stop_multiplier, sector=sector, sleeve=sleeve or "MIDSMALLCAP400",
                cost=value + charges,
            )

        trade = Trade(date=on, symbol=symbol, side="buy", qty=qty, price=round(price, 4),
                      value=round(value, 2), charges=round(charges, 2), reason=reason)
        self.trades.append(trade)
        return trade

    def sell(self, symbol: str, reference_price: float, on: dt.date,
             *, reason: str = "", fill_price: float | None = None) -> Trade | None:
        """Close a position entirely. `fill_price` overrides the slippage model
        (used for stop fills, where the gap itself is the adverse price)."""
        h = self.holdings.get(symbol)
        if h is None:
            return None
        price = fill_price if fill_price is not None else self.costs.fill_price(reference_price, "sell")
        value = h.qty * price
        charges = self.costs.charges(value)

        self.cash += value - charges
        del self.holdings[symbol]

        pnl = (value - charges) - h.basis()
        tax = 0.0
        if self.tax is not None:
            tax = self.tax.realise(on, pnl, (on - h.entry_date).days)
            self.cash -= tax

        trade = Trade(date=on, symbol=symbol, side="sell", qty=h.qty, price=round(price, 4),
                      value=round(value, 2), charges=round(charges, 2), reason=reason,
                      pnl=round(pnl, 2), tax=round(tax, 2))
        self.trades.append(trade)
        return trade

    def liquidate(self, prices: dict[str, float], on: dt.date, reason: str) -> list[Trade]:
        """Sell everything priceable (RED regime). Unpriceable names are held."""
        out: list[Trade] = []
        for sym in self.symbols():
            px = prices.get(sym)
            if px is not None:
                t = self.sell(sym, px, on, reason=reason)
                if t:
                    out.append(t)
        return out

    # --- the RED-regime parking asset ---------------------------------------
    def buy_alt(self, symbol: str, reference_price: float, budget: float,
                on: dt.date) -> Trade | None:
        """Park cash in the alt asset. No stop: it is a parking spot, not a bet."""
        price = self.costs.fill_price(reference_price, "buy")
        if price <= 0 or budget <= 0:
            return None
        qty = int(min(budget, self.cash) // price)
        if qty < 1:
            return None

        value = qty * price
        charges = self.costs.charges(value)
        if value + charges > self.cash:
            qty -= 1
            if qty < 1:
                return None
            value = qty * price
            charges = self.costs.charges(value)

        self.cash -= value + charges
        if self.alt and self.alt.symbol == symbol:
            total = self.alt.qty + qty
            self.alt.cost = self.alt.basis() + value + charges
            self.alt.entry_price = (self.alt.qty * self.alt.entry_price + value) / total
            self.alt.qty = total
        else:
            self.alt = Holding(symbol=symbol, qty=qty, entry_price=price,
                               entry_date=on, stop_price=0.0, sleeve="ALT",
                               cost=value + charges)

        trade = Trade(date=on, symbol=symbol, side="buy", qty=qty, price=round(price, 4),
                      value=round(value, 2), charges=round(charges, 2), reason="park_alt")
        self.trades.append(trade)
        return trade

    def sell_alt(self, reference_price: float, on: dt.date,
                 reason: str = "unpark_alt") -> Trade | None:
        if self.alt is None:
            return None
        h = self.alt
        price = self.costs.fill_price(reference_price, "sell")
        value = h.qty * price
        charges = self.costs.charges(value)

        self.cash += value - charges
        self.alt = None

        # The parking asset is not listed equity: its gain is taxed as income at
        # the slab rate, and a loss on it shelters nothing in this model.
        pnl = (value - charges) - h.basis()
        tax = 0.0
        if self.tax is not None:
            tax = self.tax.cash_income(on, pnl)
            self.cash -= tax

        trade = Trade(date=on, symbol=h.symbol, side="sell", qty=h.qty, price=round(price, 4),
                      value=round(value, 2), charges=round(charges, 2), reason=reason,
                      pnl=round(pnl, 2), tax=round(tax, 2))
        self.trades.append(trade)
        return trade
