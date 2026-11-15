"""Portfolio audit — the rank-exit rule (Spec v2.3 §2.6) — pure.

A holding leaves when it ranks worse than the threshold, or when it is no
longer in the scanned universe at all (dropped from the index, or gone
ineligible on liquidity/listing age — both mean "we would not buy it today").

A holding is NOT flagged when the scan simply could not rank it:

  * no current price (the download failed, or the last bar is stale) — the
    Rulebook's data-safeguard: a missing number is never a strike;
  * screened out by the scan's own optional sector / price-range filter — a
    discretionary screen on what to BUY is not a signal to SELL.

Such a holding is kept and marked with the reason, so the audit says plainly
that it was not audited rather than reporting an index deletion.
"""

from __future__ import annotations

from ... import config
from ...schemas.exit_flags import HoldingAudit
from ...schemas.scanner import ScannerCandidate
from .types import Position

RANK_EXIT = "rank_exit"
DROPPED = "dropped_from_universe"
# Reasons a holding is KEPT without having been ranked.
DATA_GAP = "price_data_unavailable"
SCREENED_OUT = "excluded_by_scan_filter"

_DATA_FLAGS = frozenset({"no_data", "stale_price"})
_SCREEN_FLAGS = frozenset({"sector_excluded", "below_price_min", "above_price_max"})


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
        exit_flagged = False
        flags = set(cand.flags)
        if cand.rank is None:
            if flags & _DATA_FLAGS:
                reasons.append(DATA_GAP)            # could not be priced: keep
            elif flags and flags <= _SCREEN_FLAGS:
                reasons.append(SCREENED_OUT)        # only a discretionary screen: keep
            else:
                # Genuinely ineligible (illiquid / too new) -> treat as dropped.
                reasons.append(DROPPED)
                exit_flagged = True
        elif cand.rank > rank_threshold:
            reasons.append(RANK_EXIT)
            exit_flagged = True

        rows.append(HoldingAudit(
            symbol=pos.symbol, sector=pos.sector, rank=cand.rank, score=cand.score,
            in_universe=True, exit_flagged=exit_flagged, reasons=reasons,
        ))
    return rows
