"""Part D / Step-4 — Fundamental veto service (Spec v2.2, eliminate-only).

Candidates = scanner ranks 1..VETO_RANK_WINDOW that are NOT already held
(Part D fills open slots with new names; survivors from Part C aren't re-vetoed).

Checks: (1) pledging > 20%, (2) interest coverage < 2 for non-financials,
(6) pending structural action, (7) listed < 12 months.
A strike requires CITED data; anything missing is "unverified" and passes with
a flag. This filter never ranks, picks, or adds — it only eliminates.
"""

from __future__ import annotations

import datetime as dt
import logging

from .. import config
from ..integrations import fundamentals, surveillance
from ..repositories import ledger_repository as ledger
from ..schemas.common import StepStatus
from ..schemas.cycle import Cycle, StepId
from ..schemas.fundamental import (
    NA,
    PASS,
    STRIKE,
    UNVERIFIED,
    CheckResult,
    FundamentalAssessment,
    FundamentalCandidate,
    FundamentalOutput,
    PartDFundamentalRunRequest,
)
from . import cycle_service
from .errors import StepNotReady

log = logging.getLogger(__name__)


def _check_pledging(a: FundamentalAssessment) -> CheckResult:
    if a.pledging_pct is None:
        return CheckResult(verdict=UNVERIFIED, detail="no pledging data cited")
    if a.pledging_pct > config.PLEDGING_MAX_PCT:
        return CheckResult(verdict=STRIKE, value=a.pledging_pct,
                           detail=f"pledging {a.pledging_pct}% > {config.PLEDGING_MAX_PCT}%")
    return CheckResult(verdict=PASS, value=a.pledging_pct, detail=f"pledging {a.pledging_pct}%")


def _check_interest_cover(a: FundamentalAssessment, auto: float | None,
                          is_fin: bool) -> CheckResult:
    if is_fin:
        return CheckResult(verdict=NA, detail="financial sector — check not applicable")
    value = a.interest_coverage if a.interest_coverage is not None else auto
    if value is None:
        return CheckResult(verdict=UNVERIFIED, detail="no income-statement data")
    if value < config.MIN_INTEREST_COVERAGE:
        return CheckResult(verdict=STRIKE, value=value,
                           detail=f"interest coverage {value} < {config.MIN_INTEREST_COVERAGE}")
    shown = ">100" if value >= fundamentals.NO_DEBT_IC else str(value)
    return CheckResult(verdict=PASS, value=value, detail=f"interest coverage {shown}")


def _check_pending_action(a: FundamentalAssessment, surveillance: str) -> CheckResult:
    # NSE surveillance is a cited regulatory action — it strikes automatically,
    # and a manual "none" cannot override published exchange data.
    if surveillance:
        return CheckResult(verdict=STRIKE, detail=f"under {surveillance}")
    if a.pending_action:
        return CheckResult(verdict=STRIKE, detail="pending corporate/regulatory action (manual)")
    if a.pending_action is None:
        # Surveillance was checked; mergers/SEBI cases still need a human.
        return CheckResult(verdict=UNVERIFIED, detail="not on ASM/GSM; other actions not checked")
    return CheckResult(verdict=PASS, detail="not on ASM/GSM; none pending")


def _check_listing_age(listed_days: int) -> CheckResult:
    """Auto — derived from price history, so always cited."""
    if listed_days < config.MIN_LISTING_DAYS:
        return CheckResult(verdict=STRIKE, value=float(listed_days),
                           detail=f"listed {listed_days} candles < {config.MIN_LISTING_DAYS}")
    return CheckResult(verdict=PASS, value=float(listed_days),
                       detail=f"listed {listed_days} candles")


def _evaluate(cycle: Cycle, body: PartDFundamentalRunRequest) -> FundamentalOutput:
    scan = cycle.steps.part_b_scanner.output
    if scan is None:
        raise StepNotReady("Run the Jerald scoring (Step 2) before the fundamental veto.")

    held = set(ledger.active_symbols(ledger.load_ledger()))
    pool = [
        c for c in scan.candidates
        if c.eligible and c.rank is not None
        and c.rank <= config.VETO_RANK_WINDOW
        and c.symbol not in held
    ]
    pool.sort(key=lambda c: c.rank or 10**9)
    surv_map = surveillance.fetch_surveillance()   # NSE ASM/GSM, cached daily

    rows: list[FundamentalCandidate] = []
    for c in pool:
        a = body.assessments.get(c.symbol, FundamentalAssessment())
        is_fin = fundamentals.is_financial(c.sector)
        auto_ic = fundamentals.get_interest_coverage(f"{c.symbol}.NS") if not is_fin else None
        surv = surv_map.get(c.symbol.upper(), "")

        checks = {
            "pledging": _check_pledging(a),
            "interest_cover": _check_interest_cover(a, auto_ic, is_fin),
            "pending_action": _check_pending_action(a, surv),
            "listing_age": _check_listing_age(c.listed_days),
        }
        strikes = [r.detail for r in checks.values() if r.verdict == STRIKE]
        flags = [r.detail for r in checks.values() if r.verdict == UNVERIFIED]

        rows.append(FundamentalCandidate(
            symbol=c.symbol, rank=c.rank or 0, score=c.score, sector=c.sector,
            listed_days=c.listed_days, is_financial=is_fin, auto_interest_coverage=auto_ic,
            surveillance=surv, assessment=a,
            struck=bool(strikes), strikes=strikes, flags=flags, **checks,
        ))

    struck = sum(1 for r in rows if r.struck)
    unverified = sum(1 for r in rows if r.flags and not r.struck)
    actions = [
        f"Evaluated ranks 1-{config.VETO_RANK_WINDOW} excluding {len(held)} held name(s): "
        f"{len(rows)} candidate(s), {struck} struck, {len(rows) - struck} surviving.",
        "Strikes require cited data; blanks are 'unverified' and pass with a flag."
        if unverified else "All checks cited.",
    ]
    return FundamentalOutput(
        as_of=scan.as_of, rank_window=config.VETO_RANK_WINDOW, candidates=rows,
        struck_count=struck, passed_count=len(rows) - struck, actions=actions,
    )


def run(body: PartDFundamentalRunRequest, now_ist: dt.datetime | None = None) -> Cycle:
    """Evaluate the fundamental veto. Pure computation — nothing is committed."""
    now_ist = now_ist or dt.datetime.now(config.IST)
    cycle = cycle_service.get_current_cycle()
    cycle_service.require_open(cycle)
    if cycle.current_step != StepId.PART_D_FUNDAMENTAL.value:
        raise StepNotReady("Step 4 is available only after the portfolio audit advances.")

    step = cycle.steps.part_d_fundamental
    step.status = StepStatus.IN_PROGRESS
    step.started_at = now_ist
    step.error = None
    step.input = {"assessments": {k: v.model_dump() for k, v in body.assessments.items()}}

    try:
        step.output = _evaluate(cycle, body)
        step.status = StepStatus.DONE
    except Exception as exc:  # noqa: BLE001
        log.exception("Step 4 failed for cycle %s", cycle.cycle_id)
        step.status = StepStatus.FAILED
        step.output = None
        step.error = f"{type(exc).__name__}: {exc}"
    finally:
        step.completed_at = now_ist

    return cycle_service.touch_and_save(cycle, now_ist)
