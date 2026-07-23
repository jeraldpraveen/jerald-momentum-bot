"""The pure strategy core — the rules, with no I/O and no app state.

Every decision in the Rulebook lives here as a function of explicit inputs.
Two callers share it: the live Friday-ritual services (`app/services/`), which
load a Cycle and the CSV ledger around it, and the backtest engine
(`app/backtest/`), which drives it over historical weeks. Neither owns the
rules, so the backtest provably tests what you actually trade.

Nothing in here may import from `app.services`, `app.repositories` or
`app.routers` — that constraint is what keeps it reusable, and it is enforced
by a test (`test_strategy_core_has_no_io`).
"""

from .audit import DROPPED, RANK_EXIT, audit_holdings
from .caps import CapsResult, apply_caps, sleeve_of
from .regime import GREEN, RED, decide_regime
from .scoring import build_candidates, median_traded_value_cr
from .technical import check_extension, check_overbought, check_trend, evaluate_technical
from .types import Position, RankedName

__all__ = [
    "GREEN", "RED", "decide_regime",
    "build_candidates", "median_traded_value_cr",
    "audit_holdings", "RANK_EXIT", "DROPPED",
    "evaluate_technical", "check_trend", "check_extension", "check_overbought",
    "apply_caps", "CapsResult", "sleeve_of",
    "Position", "RankedName",
]
