"""Transaction costs and slippage.

Indian equity delivery often has zero brokerage, but STT, exchange charges,
stamp duty and GST are real, and on mid/microcaps slippage usually dwarfs all
of them. Both are basis-point knobs so a run can show how sensitive the edge is
to execution quality — a momentum strategy that only works at zero cost is not
a strategy.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config as bt_config

BPS = 10_000.0


@dataclass(frozen=True)
class CostModel:
    cost_bps: float = bt_config.COST_BPS
    slippage_bps: float = bt_config.SLIPPAGE_BPS

    def fill_price(self, reference: float, side: str) -> float:
        """Adverse fill: you buy a little higher and sell a little lower."""
        slip = reference * self.slippage_bps / BPS
        return reference + slip if side == "buy" else reference - slip

    def charges(self, value: float) -> float:
        """Charges on a trade of `value` rupees (always a cost, never a credit)."""
        return abs(value) * self.cost_bps / BPS
