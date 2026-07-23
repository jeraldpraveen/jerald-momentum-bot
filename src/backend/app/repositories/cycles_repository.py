"""Cycle persistence: one JSON file per weekly cycle + a current-cycle pointer.

This is the ONLY layer that touches the cycle files. The cycle JSON *is* the
journal — it supersedes the old journal.md / journal.jsonl / state.json trio.
Writes are atomic (temp file + replace) so a crash mid-save can't corrupt a
cycle. Swapping to a real DB later means reimplementing only this module.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .. import config
from ..schemas.cycle import Cycle, CycleSummary


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _cycle_path(cycle_id: str) -> Path:
    return config.CYCLES_DIR / f"{cycle_id}.json"


# --- current-cycle pointer -------------------------------------------------

def get_current_cycle_id() -> str | None:
    if not config.CURRENT_CYCLE_FILE.exists():
        return None
    data = json.loads(config.CURRENT_CYCLE_FILE.read_text(encoding="utf-8"))
    return data.get("cycle_id")


def set_current_cycle_id(cycle_id: str | None) -> None:
    _write_atomic(config.CURRENT_CYCLE_FILE, json.dumps({"cycle_id": cycle_id}, indent=2))


# --- cycle CRUD ------------------------------------------------------------

def exists(cycle_id: str) -> bool:
    return _cycle_path(cycle_id).exists()


def load(cycle_id: str) -> Cycle | None:
    path = _cycle_path(cycle_id)
    if not path.exists():
        return None
    return Cycle.model_validate_json(path.read_text(encoding="utf-8"))


def save(cycle: Cycle) -> Cycle:
    _write_atomic(_cycle_path(cycle.cycle_id), cycle.model_dump_json(indent=2))
    return cycle


def delete(cycle_id: str) -> bool:
    """Permanently remove a cycle file. Returns True if it existed."""
    path = _cycle_path(cycle_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def list_cycles() -> list[CycleSummary]:
    if not config.CYCLES_DIR.exists():
        return []
    summaries: list[CycleSummary] = []
    for path in config.CYCLES_DIR.glob("*.json"):
        try:
            cyc = Cycle.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — one bad file must not hide all history
            import logging
            logging.getLogger(__name__).warning(
                "Skipping unreadable cycle file %s (%s)", path.name, type(exc).__name__)
            continue
        summaries.append(
            CycleSummary(
                cycle_id=cyc.cycle_id, status=cyc.status, outcome=cyc.outcome,
                current_step=cyc.current_step, created_at=cyc.created_at,
                updated_at=cyc.updated_at,
            )
        )
    return sorted(summaries, key=lambda s: s.cycle_id, reverse=True)


def get_previous_regime(before_cycle_id: str) -> str | None:
    """Regime of the most recent cycle strictly before `before_cycle_id`.

    Reads that cycle's Part A output. Used for RED -> GREEN re-entry detection,
    replacing the old standalone state.json.
    """
    prior = [
        s.cycle_id for s in list_cycles()
        if s.cycle_id < before_cycle_id
    ]
    for cid in prior:  # list_cycles is newest-first, so first match is newest prior
        cyc = load(cid)
        if cyc is None:
            continue
        out = cyc.steps.part_a_regime_gate.output
        if out is not None:
            return out.regime
    return None
