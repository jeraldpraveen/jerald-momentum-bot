"""Part A — Regime Gate Evaluation (Friday close ritual, steps 2.1–2.3).

RED  (close below weekly Supertrend): mark all holdings for Monday-open
     liquidation, cancel all resting GTT stops, journal, terminate.
GREEN (close above): journal and hand off to Part B. If last week was RED,
     flag the mandatory RED->GREEN re-entry for Monday open.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

from . import config, ledger
from .broker import Broker, DryRunBroker
from .data_fetch import fetch_weekly_index_data
from .journal import write_journal
from .supertrend import compute_supertrend

log = logging.getLogger(__name__)

GREEN, RED = "GREEN", "RED"


def _load_state() -> dict:
    if config.STATE_FILE.exists():
        return json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
    return {}


def _save_state(state: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def run_regime_gate(broker: Broker | None = None, now_ist: dt.datetime | None = None) -> dict:
    """Evaluate the gate and execute the RED/GREEN branch. Returns the journal entry."""
    broker = broker or DryRunBroker()
    now_ist = now_ist or dt.datetime.now(config.IST)

    # Step 2.1 — weekly candles
    data = fetch_weekly_index_data(now_ist)

    # Step 2.2 — Supertrend (1, 2.5)
    st = compute_supertrend(
        data.weekly,
        period=config.SUPERTREND_ATR_PERIOD,
        multiplier=config.SUPERTREND_MULTIPLIER,
    )
    last = st.iloc[-1]
    week_end = st.index[-1].date()
    regime = GREEN if last["trend"] == 1 else RED

    prev_state = _load_state()
    prev_regime = prev_state.get("regime")
    reentry_required = regime == GREEN and prev_regime == RED

    log.info("Week ending %s: close=%.2f supertrend=%.2f -> regime %s (prev %s)",
             week_end, last["close"], last["supertrend"], regime, prev_regime or "n/a")

    actions: list[str] = []
    holdings = ledger.load_ledger()

    # Step 2.3 — branch on gate status
    if regime == RED:
        holdings, marked = ledger.mark_all_for_liquidation(
            holdings, reason="REGIME_RED", as_of=week_end
        )
        ledger.save_ledger(holdings)
        if marked:
            actions.append(f"Marked {len(marked)} holdings PENDING_EXIT for Monday open: {', '.join(marked)}")
        else:
            actions.append("No ACTIVE holdings to mark")
        broker.cancel_all_gtts()
        actions.append("Cancelled all resting GTT stops")
        actions.append("Scan/rank/veto skipped — terminating")
    else:
        if reentry_required:
            actions.append("RED->GREEN shift: mandatory re-entry Monday open (top ~10, ~10% weights, fresh 20% stops)")
        actions.append("Proceed to Part B (scan, veto, fill slots)")

    entry = {
        "run_at": now_ist.strftime("%Y-%m-%d %H:%M"),
        "week_end": week_end.isoformat(),
        "regime": regime,
        "previous_regime": prev_regime,
        "reentry_required": reentry_required,
        "index_close": round(float(last["close"]), 2),
        "supertrend": round(float(last["supertrend"]), 2),
        "data_source": data.source,
        "active_holdings": holdings.loc[holdings["status"] == "ACTIVE", "symbol"].tolist(),
        "pending_exit": holdings.loc[holdings["status"] == "PENDING_EXIT", "symbol"].tolist(),
        "actions": actions,
    }
    write_journal(entry)
    _save_state({"regime": regime, "week_end": week_end.isoformat(),
                 "reentry_required": reentry_required, "updated_at": str(now_ist)})
    return entry
