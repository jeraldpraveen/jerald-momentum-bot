"""NSE surveillance lists (ASM / GSM) — the automatable part of the "pending
structural corporate or regulatory action" veto (Spec v2.2, check 6).

A stock placed under Additional or Graded Surveillance IS a pending regulatory
action, and it's the one that matters most in practice: surveillance brings
higher margins, trade-to-trade settlement or periodic call auctions, which can
badly restrict your ability to EXIT a position.

NSE publishes both as JSON, so this check is cited data rather than judgement.
Everything else in check 6 (mergers, schemes, SEBI cases) still needs a human.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import requests

from .. import config

log = logging.getLogger(__name__)

_ASM_URL = "https://www.nseindia.com/api/reportASM"
_GSM_URL = "https://www.nseindia.com/api/reportGSM"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}


def _rows(payload) -> list[dict]:
    """ASM returns {longterm:{data:[...]}, shortterm:{...}}; GSM returns a list."""
    if isinstance(payload, list):
        return payload
    out: list[dict] = []
    if isinstance(payload, dict):
        for section in payload.values():
            if isinstance(section, dict) and isinstance(section.get("data"), list):
                out.extend(section["data"])
    return out


def _fetch(url: str) -> list[dict]:
    r = requests.get(url, headers=_HEADERS, timeout=25)
    r.raise_for_status()
    return _rows(r.json())


def fetch_surveillance(today: dt.date | None = None) -> dict[str, str] | None:
    """Return {SYMBOL: reason}, cached per day — or None if NSE is unreachable.

    None and {} mean different things and the caller must not confuse them:
    {} is "checked, nothing is under surveillance"; None is "could not check".
    """
    today = today or dt.date.today()
    path = config.SURVEILLANCE_CACHE
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("fetched") == today.isoformat():
                return cached.get("symbols", {})
        except Exception:  # noqa: BLE001 — a bad cache just triggers a refetch
            pass

    symbols: dict[str, str] = {}
    try:
        for row in _fetch(_ASM_URL):
            sym = str(row.get("symbol") or "").strip().upper()
            if sym:
                stage = row.get("asmSurvIndicator") or row.get("survDesc") or "ASM"
                symbols[sym] = f"NSE ASM ({stage})"
        for row in _fetch(_GSM_URL):
            sym = str(row.get("symbol") or "").strip().upper()
            if sym:
                stage = row.get("survDesc") or row.get("gsmStage") or "GSM"
                symbols[sym] = f"NSE GSM ({stage})"
    except Exception as exc:  # noqa: BLE001 — unreachable => fall back to manual
        log.info("NSE surveillance lists unavailable (%s)", type(exc).__name__)
        return None

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched": today.isoformat(), "symbols": symbols}, indent=2),
                    encoding="utf-8")
    log.info("Surveillance lists: %d symbols under ASM/GSM", len(symbols))
    return symbols
