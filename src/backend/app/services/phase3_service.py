"""Phase 3 / Step-8 — Monday execution plan (Spec v2.2 §3.1-3.4).

Builds the by-hand checklist for the open. Produces instructions and indicative
quantities only — no broker calls, no orders (manual until the Jan-2027
promotion). Works for a RED week too, where the plan is exits-only.
"""

from __future__ import annotations

import datetime as dt
import logging
import math

from .. import config
from ..integrations import stock_prices
from ..repositories import ledger_repository as ledger
from ..schemas.common import StepStatus
from ..schemas.cycle import Cycle, StepId
from ..schemas.phase3 import BuyOrder, ExitOrder, Phase3Output, Phase3RunRequest
from . import cycle_service
from .errors import StepNotReady

log = logging.getLogger(__name__)

STOP_MULTIPLIER = 0.80          # stop = fill price x 0.80 (20% stop)


def _last_closes(symbols: list[str], as_of: dt.date | None) -> dict[str, float]:
    if not symbols:
        return {}
    prices = stock_prices.get_prices([f"{s}.NS" for s in symbols], as_of=as_of)
    out: dict[str, float] = {}
    for s in symbols:
        df = prices.get(f"{s}.NS")
        if df is not None and not df.empty:
            out[s] = round(float(df["close"].iloc[-1]), 2)
    return out


def _build(cycle: Cycle, body: Phase3RunRequest) -> Phase3Output:
    gate = cycle.steps.part_a_regime_gate.output
    if gate is None:
        raise StepNotReady("Run the regime gate (Step 1) before building the Monday plan.")
    caps = cycle.steps.part_d_risk_caps.output          # None on a RED week
    as_of_date = dt.date.fromisoformat(gate.week_end)

    df = ledger.load_ledger()
    pending = df[df["status"] == "PENDING_EXIT"]
    kept = df[df["status"] == "ACTIVE"]

    buy_syms = [b.symbol for b in caps.buys] if caps else []
    closes = _last_closes(list(pending["symbol"]) + list(kept["symbol"]) + buy_syms, as_of_date)

    warnings: list[str] = []

    # --- 3.1 exits ---------------------------------------------------------
    exits: list[ExitOrder] = []
    est_proceeds = 0.0
    for row in pending.to_dict(orient="records"):
        sym = str(row["symbol"])
        qty = ledger._num(row.get("qty"), int)
        close = closes.get(sym)
        proceeds = round(qty * close, 2) if (qty and close) else None
        if proceeds:
            est_proceeds += proceeds
        if qty is None:
            warnings.append(f"{sym}: no quantity in the ledger — enter it in Step-0 to size the sell.")
        exits.append(ExitOrder(
            symbol=sym, qty=qty,
            existing_stop=ledger._num(row.get("stop_price"), float),
            last_close=close, est_proceeds=proceeds,
            reason=str(row.get("exit_reason") or "").strip(),
        ))

    # --- capital basis: position SIZE vs fundable CASH ---------------------
    # These are two different numbers. `capital` sets how big a 10% position is;
    # `available` limits how many of them you can actually buy on Monday.
    held_value = 0.0
    for row in kept.to_dict(orient="records"):
        qty = ledger._num(row.get("qty"), int)
        close = closes.get(str(row["symbol"]))
        if qty and close:
            held_value += qty * close

    if body.portfolio_capital:
        capital, source = float(body.portfolio_capital), "entered"
    else:
        capital = round(held_value + est_proceeds, 2) or None
        source = "estimated from ledger"
        if capital:
            warnings.append(
                "Capital estimated from the ledger (holdings at last close + exit proceeds). "
                "Enter your actual portfolio value for accurate sizing.")

    if body.available_cash is not None:
        available, cash_source = float(body.available_cash), "entered"
    elif capital:
        # Everything you are NOT keeping is deployable: the exiting positions turn
        # to cash, and any capital beyond your held value is idle cash already.
        # available = total capital - value of the holdings you keep.
        available = round(max(0.0, capital - held_value), 2)
        cash_source = "capital minus kept holdings"
    else:
        available, cash_source = round(est_proceeds, 2), "from exits"

    # Over-concentration flag: the strategy targets ~10% per name and never
    # trims winners, so if existing holdings are well above target they crowd
    # out new buys. Say so rather than let the cash shortfall look mysterious.
    if capital:
        target_value_full = capital * 10.0 / 100
        heavy = []
        for row in kept.to_dict(orient="records"):
            qty = ledger._num(row.get("qty"), int)
            close = closes.get(str(row["symbol"]))
            if qty and close and qty * close > target_value_full * 1.5:
                heavy.append(f"{row['symbol']} ({round(100 * qty * close / capital)}%)")
        if heavy:
            warnings.append(
                f"Above the 10% target: {', '.join(heavy)}. The strategy will not trim "
                "these, but they leave less cash for new names — expect fewer buys than open slots.")

    # --- 3.2 buys: fund in RANK ORDER up to available cash -----------------
    buys: list[BuyOrder] = []
    est_deployment = 0.0
    remaining = available
    ranked = sorted((caps.buys if caps else []), key=lambda b: b.rank)
    for b in ranked:
        close = closes.get(b.symbol)
        target = round(capital * b.weight_pct / 100, 2) if capital else None
        funded, defer, qty = True, "", None

        if target and close and target <= remaining + 1:      # +1 for rounding slack
            qty = int(math.floor(target / close))
            if qty >= 1:
                spent = qty * close
                est_deployment += spent
                remaining -= spent
            else:
                funded, defer = False, "one share exceeds the target size"
        elif not (target and close):
            funded, defer = False, "no price"
        else:
            funded, defer = False, "insufficient cash"

        buys.append(BuyOrder(
            symbol=b.symbol, rank=b.rank, sector=b.sector, target_weight_pct=b.weight_pct,
            target_value=target, last_close=close,
            indicative_qty=qty, funded=funded, defer_reason=defer,
            indicative_stop=round(close * STOP_MULTIPLIER, 2) if close else None,
        ))

    funded_buys = [b for b in buys if b.funded]
    deferred_buys = [b for b in buys if not b.funded]
    leftover = round(remaining, 2)
    if deferred_buys and any(b.defer_reason == "insufficient cash" for b in deferred_buys):
        names = ", ".join(f"{b.symbol} (rank {b.rank})" for b in deferred_buys
                          if b.defer_reason == "insufficient cash")
        warnings.append(
            f"Deferred for want of cash: {names}. Funded the top {len(funded_buys)} by rank with "
            f"Rs {available:,.0f}; the rest wait until exits free more cash. Do NOT under-size "
            "them to fit — the strategy holds ~10% positions, not more, smaller ones.")
    if available and not funded_buys and (caps and caps.buys):
        warnings.append(
            f"Available cash Rs {available:,.0f} is below one 10% position "
            f"(Rs {capital * 0.10:,.0f}). Nothing fits without exiting more or adding cash.")

    # --- the checklist -----------------------------------------------------
    checklist: list[str] = []
    if exits:
        checklist.append("09:15 — LIQUIDATIONS FIRST (one ticker at a time):")
        for e in exits:
            stop = f" (cancel its GTT at {e.existing_stop})" if e.existing_stop else " (cancel its GTT)"
            qty_txt = f"{e.qty} sh" if e.qty else "your full position"
            checklist.append(f"   • {e.symbol}{stop}, then SELL {qty_txt} at market.")
        checklist.append("   Never bulk-cancel GTTs — cancel each only as you place its sell.")
    else:
        checklist.append("09:15 — No exits this week.")

    if funded_buys:
        checklist.append(
            f"Then — DEPLOY Rs {round(est_deployment):,.0f} of Rs {available:,.0f} available "
            "(~10% each, best rank first):")
        for b in funded_buys:
            qty_txt = f"~{b.indicative_qty} sh" if b.indicative_qty else "size to ~10%"
            val = f" (~Rs {b.target_value:,.0f})" if b.target_value else ""
            checklist.append(f"   • BUY {b.symbol} {qty_txt}{val} — rank {b.rank}, {b.sector}")
        checklist.append("Immediately after each fill — place a GTT stop at FILL x 0.80:")
        for b in funded_buys:
            hint = f" (~{b.indicative_stop} if it fills near {b.last_close})" if b.indicative_stop else ""
            checklist.append(f"   • {b.symbol} stop{hint}")
    elif caps and caps.buys:
        checklist.append("No buys fit your available cash this week — see the note below.")
    else:
        checklist.append("No buys this week — that capital stays in cash until Friday.")

    if deferred_buys:
        checklist.append(
            f"DEFERRED (no cash this week): "
            f"{', '.join(f'{b.symbol} (rank {b.rank})' for b in deferred_buys)}. "
            "They stay candidates — re-check next Friday.")
    if leftover > 100:
        checklist.append(
            f"Rs {leftover:,.0f} stays in cash — a slot short of the next 10% buy. "
            "Park it in your liquid fund, don't force an undersized position.")

    checklist.append("Leave grandfathered legacy positions untouched — never trim them to 10%.")
    checklist.append("Done — clear the queues and return to mid-week monitoring (stops only).")

    actions = [
        f"{gate.regime} week — {len(exits)} exit(s), {len(funded_buys)} buy(s)"
        + (f", {len(deferred_buys)} deferred" if deferred_buys else "") + ".",
        "Execute by hand at the open; this tool places no orders.",
    ]
    return Phase3Output(
        as_of=gate.week_end, regime=gate.regime,
        capital_basis=capital, capital_source=source if capital else "",
        available_cash=available or None, cash_source=cash_source if available else "",
        deployable=round(est_deployment, 2) or None, leftover_cash=leftover or None,
        funded_count=len(funded_buys), deferred_count=len(deferred_buys),
        stop_pct=round((1 - STOP_MULTIPLIER) * 100, 0),
        exits=exits, buys=buys,
        est_proceeds=round(est_proceeds, 2) or None,
        est_deployment=round(est_deployment, 2) or None,
        checklist=checklist, warnings=warnings, actions=actions,
    )


def run(body: Phase3RunRequest, now_ist: dt.datetime | None = None) -> Cycle:
    """Build the Monday checklist. Read-only — places no orders."""
    now_ist = now_ist or dt.datetime.now(config.IST)
    cycle = cycle_service.get_current_cycle()
    cycle_service.require_open(cycle)
    if cycle.current_step != StepId.PHASE3_PLAN.value:
        raise StepNotReady("Step 8 is available only after the journal is committed.")

    step = cycle.steps.phase3_plan
    step.status = StepStatus.IN_PROGRESS
    step.started_at = now_ist
    step.error = None
    step.input = body.model_dump()

    try:
        step.output = _build(cycle, body)
        step.status = StepStatus.DONE
    except Exception as exc:  # noqa: BLE001
        log.exception("Step 8 failed for cycle %s", cycle.cycle_id)
        step.status = StepStatus.FAILED
        step.output = None
        step.error = f"{type(exc).__name__}: {exc}"
    finally:
        step.completed_at = now_ist

    return cycle_service.touch_and_save(cycle, now_ist)
