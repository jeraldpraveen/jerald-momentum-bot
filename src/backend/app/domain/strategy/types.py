"""Neutral inputs for the strategy core.

The core must not know whether a position came from the live CSV ledger or from
a simulated backtest portfolio, so callers hand it these plain records instead
of a DataFrame or a Cycle.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Position:
    """A held name, as far as the rules are concerned."""

    symbol: str
    sector: str = ""
    sleeve: str = ""


@dataclass(frozen=True)
class RankedName:
    """A scored candidate carried between veto stages."""

    symbol: str
    rank: int
    sector: str = ""
