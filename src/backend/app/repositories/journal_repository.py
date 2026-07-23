"""The durable weekly journal — the only layer that writes journal files.

Two sinks, appended once per cycle when Part E is advanced:
  * journal.jsonl — full machine-readable entry (one JSON object per week)
  * journal.md    — a human-readable table row

This deliberately lives OUTSIDE the cycle files: CLOSING a cycle discards
nothing — the journal is the permanent record of what was decided, and it is
what the backtest will read. DELETING a cycle is the one exception: that means
"this run never counted", so `delete_entries` takes its rows out too.
"""

from __future__ import annotations

import json
import os

from .. import config
from ..schemas.journal import JournalEntry

MD_HEADER = (
    "# Strategy Journal\n\n"
    "| Date | Cycle | Regime | Holdings (rank) | Exits | Buys | Strike-outs | "
    "Unfilled | Stops | Notes |\n"
    "| :-- | :-- | :-- | :-- | :-- | :-- | :-- | --: | :-- | :-- |\n"
)


def _cell(text: str) -> str:
    """Markdown-table safe: no pipes or newlines."""
    return (text or "—").replace("|", "/").replace("\n", " ")


def _md_row(e: JournalEntry) -> str:
    holdings = ", ".join(
        f"{h.symbol}({h.rank if h.rank is not None else '∞'})" for h in e.holdings)
    exits = ", ".join(e.exits)
    buys = ", ".join(f"{b.symbol}({b.rank})" for b in e.buys)
    strikes = "; ".join(f"{s.symbol}: {', '.join(s.reasons)}" for s in e.strikeouts)
    stops = "Y" if e.stops_verified else ("N" if e.stops_verified is False else "—")
    return (
        f"| {e.date} | {e.cycle_id} | **{e.regime}** | {_cell(holdings)} | {_cell(exits)} "
        f"| {_cell(buys)} | {_cell(strikes)} | {e.unfilled_slots} | {stops} "
        f"| {_cell(e.notes)} |\n"
    )


def append(entry: JournalEntry) -> None:
    """Append one weekly entry to both sinks."""
    config.JOURNAL_JSONL.parent.mkdir(parents=True, exist_ok=True)

    with open(config.JOURNAL_JSONL, "a", encoding="utf-8") as f:
        f.write(entry.model_dump_json() + "\n")

    if not config.JOURNAL_MD.exists():
        config.JOURNAL_MD.write_text(MD_HEADER, encoding="utf-8")
    with open(config.JOURNAL_MD, "a", encoding="utf-8") as f:
        f.write(_md_row(entry))


def delete_entries(cycle_id: str) -> int:
    """Remove every entry written by `cycle_id`. Returns how many were dropped.

    Only used when a cycle is DELETED (discarding a throwaway/test run) — closing
    a cycle never touches the journal. The markdown file is regenerated from the
    surviving JSONL rows rather than line-edited, so the two sinks cannot drift
    apart if a row was ever hand-edited.
    """
    if not config.JOURNAL_JSONL.exists():
        return 0

    kept = [e for e in _read_all() if e.cycle_id != cycle_id]
    removed = len(_read_all()) - len(kept)
    if removed == 0:
        return 0

    _write_atomic(config.JOURNAL_JSONL,
                  "".join(e.model_dump_json() + "\n" for e in kept))
    _write_atomic(config.JOURNAL_MD,
                  MD_HEADER + "".join(_md_row(e) for e in kept))
    return removed


def _write_atomic(path, text: str) -> None:
    """Temp file + replace, so an interrupted write cannot truncate the journal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read_all() -> list[JournalEntry]:
    if not config.JOURNAL_JSONL.exists():
        return []
    return [
        JournalEntry.model_validate_json(line)
        for line in config.JOURNAL_JSONL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_entries(limit: int | None = None) -> list[JournalEntry]:
    """Most recent first."""
    rows = _read_all()
    rows.reverse()
    return rows[:limit] if limit else rows
