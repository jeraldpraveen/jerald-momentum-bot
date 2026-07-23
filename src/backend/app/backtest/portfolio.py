"""Simulated portfolio — positions, cash, stops, and the trade log.

Deliberately dumb about strategy: it executes what it is told and keeps the
books. Every decision about WHAT to trade comes from the shared strategy core.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .costs import CostModel


@dataclass
class Holding:
    symbol: str
    qty: int
    entry_price: float          # actual fill, after slippage
    entry_date: dt.date
    stop_price: float
    sector: str = ""
    sleeve: str = "MIDSMALLCAP400"

    def value(self, price: float) -> float:
        return self.qty * price


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


@dataclass
class Portfolio:
    cash: float
    costs: CostModel = field(default_factory=CostModel)
    holdings: dict[str, Holding] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
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
            existing.entry_price = (existing.value(existing.entry_price) + value) / total_qty
            existing.qty = total_qty
            existing.stop_price = existing.entry_price * stop_multiplier
        else:
            self.holdings[symbol] = Holding(
                symbol=symbol, qty=qty, entry_price=price, entry_date=on,
                stop_price=price * stop_multiplier, sector=sector, sleeve=sleeve or "MIDSMALLCAP400",
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

        trade = Trade(date=on, symbol=symbol, side="sell", qty=h.qty, price=round(price, 4),
                      value=round(value, 2), charges=round(charges, 2), reason=reason)
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
            self.alt.entry_price = (self.alt.qty * self.alt.entry_price + value) / total
            self.alt.qty = total
        else:
            self.alt = Holding(symbol=symbol, qty=qty, entry_price=price,
                               entry_date=on, stop_price=0.0, sleeve="ALT")

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

        trade = Trade(date=on, symbol=h.symbol, side="sell", qty=h.qty, price=round(price, 4),
                      value=round(value, 2), charges=round(charges, 2), reason=reason)
        self.trades.append(trade)
        return trade
