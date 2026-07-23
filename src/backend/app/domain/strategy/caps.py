"""Risk caps & slot filling (Spec v2.2 §2.8 + §2.10) — pure.

Walks survivors in strict rank order, adding each unless it breaches a
concentration cap. Caps are evaluated against the RESULTING portfolio (kept
holdings + buys so far), not just the new names.

Unfilled slots deliberately stay in cash: the loop never reaches past the
candidate list to force a fill, and never re-ranks or substitutes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ... import config
from ...schemas.risk_caps import MICROCAP_SLEEVE, PortfolioSlot, SelectedBuy, SkippedCandidate
from .types import Position, RankedName

SECTOR_CAP, MICROCAP_CAP, SLOTS_FULL = "sector_cap", "microcap_cap", "slots_full"
DEFAULT_SLEEVE = "MIDSMALLCAP400"


@dataclass
class CapsResult:
    kept: list[PortfolioSlot]
    buys: list[SelectedBuy]
    skipped: list[SkippedCandidate]
    open_slots: int
    unfilled_slots: int
    sector_counts: dict[str, int]
    microcap_count: int
    final_portfolio_size: int


def sleeve_of(symbol: str, microcap: set[str], ledger_sleeve: str = "") -> str:
    """Microcap membership wins over whatever the ledger recorded."""
    if symbol.upper() in microcap:
        return MICROCAP_SLEEVE
    return ledger_sleeve or DEFAULT_SLEEVE


def apply_caps(
    survivors: list[RankedName],
    held: list[Position],
    microcap: set[str],
    *,
    target_max: int | None = None,
    max_per_sector: int | None = None,
    max_microcap: int | None = None,
) -> CapsResult:
    """Fill open slots from `survivors` without breaching the caps.

    Thresholds default to the frozen config values, but are resolved HERE, not
    baked into the signature — a default argument would snapshot config at
    import time and silently ignore any later override (which is exactly how a
    parameter sweep, or a test, sets them).
    """
    target_max = config.TARGET_PORTFOLIO_MAX if target_max is None else target_max
    max_per_sector = config.MAX_PER_SECTOR if max_per_sector is None else max_per_sector
    max_microcap = config.MAX_MICROCAP if max_microcap is None else max_microcap

    kept = [
        PortfolioSlot(symbol=p.symbol, sector=p.sector,
                      sleeve=sleeve_of(p.symbol, microcap, p.sleeve))
        for p in held
    ]

    sector_counts: Counter[str] = Counter(k.sector for k in kept)
    microcap_count = sum(1 for k in kept if k.sleeve == MICROCAP_SLEEVE)
    open_slots = max(0, target_max - len(kept))

    buys: list[SelectedBuy] = []
    skipped: list[SkippedCandidate] = []

    for c in sorted(survivors, key=lambda x: x.rank):
        sleeve = sleeve_of(c.symbol, microcap)
        if len(buys) >= open_slots:
            skipped.append(SkippedCandidate(symbol=c.symbol, rank=c.rank, sector=c.sector,
                                            sleeve=sleeve, reason=SLOTS_FULL))
            continue
        if sector_counts[c.sector] + 1 > max_per_sector:
            skipped.append(SkippedCandidate(symbol=c.symbol, rank=c.rank, sector=c.sector,
                                            sleeve=sleeve, reason=SECTOR_CAP))
            continue
        if sleeve == MICROCAP_SLEEVE and microcap_count + 1 > max_microcap:
            skipped.append(SkippedCandidate(symbol=c.symbol, rank=c.rank, sector=c.sector,
                                            sleeve=sleeve, reason=MICROCAP_CAP))
            continue

        buys.append(SelectedBuy(symbol=c.symbol, rank=c.rank, sector=c.sector, sleeve=sleeve))
        sector_counts[c.sector] += 1
        if sleeve == MICROCAP_SLEEVE:
            microcap_count += 1

    return CapsResult(
        kept=kept, buys=buys, skipped=skipped,
        open_slots=open_slots, unfilled_slots=open_slots - len(buys),
        sector_counts=dict(sector_counts), microcap_count=microcap_count,
        final_portfolio_size=len(kept) + len(buys),
    )
