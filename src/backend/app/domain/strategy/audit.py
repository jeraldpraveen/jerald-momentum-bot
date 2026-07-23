"""Portfolio audit — the rank-exit rule (Spec v2.2 §2.6) — pure.

A holding leaves when it ranks worse than the threshold, or when it is no
longer in the scanned universe at all (dropped from the index, or gone
ineligible on liquidity/listing age — both mean "we would not buy it today").
"""

from __future__ import annotations

from ... import config
from ...schemas.exit_flags import HoldingAudit
from ...schemas.scanner import ScannerCandidate
from .types import Position

RANK_EXIT = "rank_exit"
DROPPED = "dropped_from_universe"


def audit_holdings(
    held: list[Position],
    candidates: list[ScannerCandidate],
    *,
    rank_threshold: int | None = None,
) -> list[HoldingAudit]:
    """Flag each held name for exit, or keep it, citing why."""
    # Resolved here, not as a default arg — see the note in caps.apply_caps.
    rank_threshold = config.RANK_EXIT_THRESHOLD if rank_threshold is None else rank_threshold
    ranked = {c.symbol: c for c in candidates}
    rows: list[HoldingAudit] = []

    for pos in held:
        cand = ranked.get(pos.symbol)
        if cand is None:
            rows.append(HoldingAudit(
                symbol=pos.symbol, sector=pos.sector, rank=None, score=None,
                in_universe=False, exit_flagged=True, reasons=[DROPPED]))
            continue

        reasons: list[str] = []
        # An ineligible name (illiquid / too new) has no rank -> treat as dropped.
        if cand.rank is None:
            reasons.append(DROPPED)
        elif cand.rank > rank_threshold:
            reasons.append(RANK_EXIT)

        rows.append(HoldingAudit(
            symbol=pos.symbol, sector=pos.sector, rank=cand.rank, score=cand.score,
            in_universe=True, exit_flagged=bool(reasons), reasons=reasons,
        ))
    return rows
